"""Validate a submission folder and package it as submission.zip.

Usage:
    python scripts/build_submission.py                  # check submission/, write dist/submission.zip
    python scripts/build_submission.py --check-only     # check only, write nothing
    python scripts/build_submission.py --src experiments/variants/v1_workflow

The checks encode the competition rules we know of. Constants marked
UNVERIFIED come from other teams' notes, not from the official harness, and
must be confirmed against data/HARNESS_README.md before a real submission.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

ALLOWED_MODELS = frozenset({
    "gemma-4-31b-it-qat-w4a16-ct",
    # UNVERIFIED: allowed by the host's Oct 11 post, but the Model Selection
    # page still says 31b only.
    "gemma-4-12b-it-qat-w4a16-ct",
    "gemma-4-e4b-it-qat-w4a16-ct",
    "gemma-4-e2b-it-qat-w4a16-ct",
})
HARNESS_TOOLS = frozenset({
    "run_command", "read_file", "edit_file", "write_file", "submit_patch", "get_status",
    "get_code_neighbors", "search_similar_code", "get_code_subgraph",
})
# The harness adds these when an agent lists skills; declaring them yourself
# reportedly fails compilation. UNVERIFIED.
SKILL_TOOLS = frozenset({"run_skill_script", "load_skill_resource"})
# ADK resolves a bare class name from google.adk.tools, or a dotted path.
AGENT_TOOL_NAMES = frozenset({"AgentTool", "google.adk.tools.agent_tool.AgentTool"})
ALLOWED_SUFFIXES = frozenset({".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"})  # UNVERIFIED
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")

# Budget model: tasks run one after another under one cap. UNVERIFIED: the task
# count and setup time are estimates; override them with --tasks/--setup-seconds.
TIME_CAP_SECONDS = 12 * 3600
HIDDEN_TASKS = 120
SETUP_SECONDS_PER_TASK = 60
WARN_FRACTION = 0.9
TIMEOUT_KEY = "per_task_timeout_seconds"  # UNVERIFIED

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # constant timestamps keep builds byte-identical


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class Include:
    """A `!include <path>` reference, kept unresolved so it can be checked."""

    path: str


class _SubmissionLoader(yaml.SafeLoader):
    """SafeLoader that understands the harness's `!include` tag."""


def _construct_include(loader: yaml.SafeLoader, node: yaml.Node) -> Include:
    return Include(loader.construct_scalar(node))


_SubmissionLoader.add_constructor("!include", _construct_include)


class _Checker:
    def __init__(self, src: Path, tasks: int, setup_seconds: float) -> None:
        self.src = src
        self.root = src.resolve()
        self.tasks = tasks
        self.setup_seconds = setup_seconds
        self.report = Report()
        self.agents_seen: set[Path] = set()
        self.includes_seen: set[Path] = set()
        self.models: dict[str, str] = {}

    def rel(self, path: Path) -> str:
        return path.relative_to(self.src).as_posix()

    def run(self) -> Report:
        self.report.files = self.walk_files()
        root_agent = self.src / "agent.yaml"
        if root_agent.is_file():
            self.check_agent(root_agent, is_root=True)
        else:
            nested = [self.rel(p) for p in self.report.files if p.name == "agent.yaml"]
            found = f" (found {', '.join(nested)})" if nested else ""
            self.report.errors.append(f"agent.yaml must be at the archive root{found}")
        if len(set(self.models.values())) > 1:
            listing = ", ".join(f"{name}: {model}" for name, model in sorted(self.models.items()))
            self.report.errors.append(f"every agent must use the same model ({listing})")
        self.check_skills()
        self.check_adapters()
        self.check_budget()
        return self.report

    def walk_files(self) -> list[Path]:
        files = []
        for dirpath, dirnames, filenames in os.walk(self.src):
            here = Path(dirpath)
            for name in list(dirnames):
                if _is_link(here / name):
                    self.report.errors.append(f"{self.rel(here / name)}: symlinks and junctions are not allowed")
                    dirnames.remove(name)
            for name in filenames:
                path = here / name
                if _is_link(path):
                    self.report.errors.append(f"{self.rel(path)}: symlinks and junctions are not allowed")
                elif path.suffix.lower() not in ALLOWED_SUFFIXES:
                    self.report.errors.append(
                        f"{self.rel(path)}: file type not in the allow-list {sorted(ALLOWED_SUFFIXES)}")
                else:
                    files.append(path)
        return sorted(files)

    def load_yaml(self, path: Path) -> object:
        try:
            with path.open(encoding="utf-8") as fh:
                return yaml.load(fh, Loader=_SubmissionLoader)  # SafeLoader subclass
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            self.report.errors.append(f"{self.rel(path)}: cannot parse YAML ({exc})")
            return None

    def resolve_ref(self, raw: object, base: PurePosixPath, where: str) -> Path | None:
        """Check one relative file reference and return the file it points to."""
        target = self.safe_path(raw, base, where)
        if target is not None and not target.is_file():
            self.report.errors.append(f"{where}: {raw!r} not found (expected {self.rel(target)})")
            return None
        return target

    def resolve_dir(self, raw: object, where: str) -> Path | None:
        """Check a folder reference given from the archive root."""
        return self.safe_path(raw, PurePosixPath("."), where)

    def safe_path(self, raw: object, base: PurePosixPath, where: str) -> Path | None:
        """Return src/base/raw when raw is a safe relative path; otherwise record why not."""
        if not isinstance(raw, str) or not raw.strip():
            self.report.errors.append(f"{where}: path must be a non-empty string, got {raw!r}")
            return None
        if "\\" in raw:
            self.report.errors.append(f"{where}: use forward slashes in {raw!r}")
            return None
        pure = PurePosixPath(raw)
        if pure.is_absolute() or re.match(r"^[A-Za-z]:", raw):
            self.report.errors.append(f"{where}: absolute path {raw!r} is not allowed")
            return None
        if ".." in pure.parts:
            self.report.errors.append(f"{where}: '..' is not allowed in {raw!r}")
            return None
        target = self.src.joinpath(*(base / pure).parts)
        try:
            target.resolve().relative_to(self.root)
        except ValueError:
            self.report.errors.append(f"{where}: {raw!r} resolves outside the submission")
            return None
        return target

    def check_includes(self, node: object, path: Path) -> None:
        """Check every `!include` in a loaded YAML tree, following YAML targets."""
        rel = self.rel(path)
        for inc in _iter_includes(node):
            # Resolved from the archive root. UNVERIFIED: the ban on '..' only
            # makes sense if paths never need to climb out of a subfolder.
            target = self.resolve_ref(inc.path, PurePosixPath("."), f"{rel}: !include")
            if target is None:
                continue
            if path.parent != self.src:
                self.report.warnings.append(
                    f"{rel}: !include {inc.path!r} is resolved from the archive root; if the harness resolves "
                    f"it from the file's folder it breaks. Keeping agent YAML at the root avoids the question.")
            if target.suffix.lower() in {".yaml", ".yml"} and target not in self.includes_seen:
                self.includes_seen.add(target)
                self.check_includes(self.load_yaml(target), target)

    def check_agent(self, path: Path, is_root: bool = False) -> None:
        if path in self.agents_seen:
            return
        self.agents_seen.add(path)
        rel = self.rel(path)
        data = self.load_yaml(path)
        if data is None:
            return
        if not isinstance(data, dict):
            self.report.errors.append(f"{rel}: top level must be a mapping")
            return
        self.check_includes(data, path)
        base = PurePosixPath(path.parent.relative_to(self.src).as_posix())  # ADK: config_path is file-relative

        model = data.get("model")
        if model is None:
            if is_root:
                self.report.errors.append(f"{rel}: the root agent must set model")
            else:
                self.report.notes.append(f"{rel}: no model set, so ADK inherits the parent's")
        elif not isinstance(model, str) or model not in ALLOWED_MODELS:
            self.report.errors.append(f"{rel}: model {model!r} is not an allowed model {sorted(ALLOWED_MODELS)}")
        else:
            self.models[rel] = model

        tools = data.get("tools") or []
        if not isinstance(tools, list):
            self.report.errors.append(f"{rel}: tools must be a list")
            tools = []
        for i, tool in enumerate(tools):
            where = f"{rel}: tools[{i}]"
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                self.report.errors.append(f"{where}: each tool must be a mapping with a name (ADK ToolConfig)")
                continue
            name = tool["name"]
            if name in HARNESS_TOOLS:
                continue
            if name in SKILL_TOOLS:
                self.report.errors.append(
                    f"{where}: {name} is added by the harness when skills are listed; do not declare it")
            elif name in AGENT_TOOL_NAMES:
                tool_args = tool.get("args")
                agent_ref = tool_args.get("agent") if isinstance(tool_args, dict) else None
                cfg = agent_ref.get("config_path") if isinstance(agent_ref, dict) else None
                if cfg is None:
                    self.report.errors.append(f"{where}: {name} needs args.agent.config_path")
                    continue
                target = self.resolve_ref(cfg, base, f"{where} config_path")
                if target is not None:
                    self.check_agent(target)
            else:
                self.report.errors.append(f"{where}: {name!r} is not a harness tool or an AgentTool")

        sub_agents = data.get("sub_agents") or []
        if not isinstance(sub_agents, list):
            self.report.errors.append(f"{rel}: sub_agents must be a list")
            sub_agents = []
        for i, sub in enumerate(sub_agents):
            where = f"{rel}: sub_agents[{i}]"
            if not isinstance(sub, dict) or "config_path" not in sub:
                self.report.errors.append(f"{where}: only config_path references are allowed (no code)")
                continue
            self.report.warnings.append(
                f"{where}: transfer-style sub_agents are UNVERIFIED; the rules only mention AgentTool sub-agents")
            target = self.resolve_ref(sub["config_path"], base, f"{where} config_path")
            if target is not None:
                self.check_agent(target)

        adapter = data.get("adapter")  # UNVERIFIED key name and layout
        if adapter is not None:
            if isinstance(adapter, str) and "/" in adapter:
                adapter_dir = self.resolve_dir(adapter, f"{rel}: adapter")
            else:
                adapter_dir = self.src / "adapters" / str(adapter)
            if adapter_dir is not None and not adapter_dir.is_dir():
                self.report.errors.append(f"{rel}: adapter {adapter!r} not found (expected {self.rel(adapter_dir)}/)")

        skills = data.get("skills") or []
        if not isinstance(skills, list):
            self.report.errors.append(f"{rel}: skills must be a list")
            skills = []
        for i, skill in enumerate(skills):
            if not isinstance(skill, str):
                self.report.warnings.append(f"{rel}: skills[{i}] is not a string; the entry format is UNVERIFIED")
            elif not any((d / "SKILL.md").is_file() for d in (self.src / skill, self.src / "skills" / skill)):
                self.report.warnings.append(
                    f"{rel}: skills[{i}] {skill!r} does not match a folder with SKILL.md (format UNVERIFIED)")

    def check_skills(self) -> None:
        skills_dir = self.src / "skills"
        if not skills_dir.is_dir():
            return
        for skill in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            rel = self.rel(skill)
            skill_md = skill / "SKILL.md"
            if not skill_md.is_file():
                self.report.errors.append(f"{rel}: missing SKILL.md")
                continue
            meta = _frontmatter(skill_md.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or not meta.get("name"):
                self.report.errors.append(f"{rel}/SKILL.md: frontmatter must set name")

    def check_adapters(self) -> None:
        adapters_dir = self.src / "adapters"
        if not adapters_dir.is_dir():
            return
        for adapter in sorted(p for p in adapters_dir.iterdir() if p.is_dir()):
            for needed in ADAPTER_FILES:
                if not (adapter / needed).is_file():
                    self.report.errors.append(f"{self.rel(adapter)}: missing {needed}")

    def check_budget(self) -> None:
        cfg_path = self.src / "eval_config.yaml"
        if not cfg_path.is_file():
            self.report.warnings.append("no eval_config.yaml: harness defaults apply (check HARNESS_README.md)")
            return
        cfg = self.load_yaml(cfg_path)
        if cfg is None:
            return
        if not isinstance(cfg, dict):
            self.report.errors.append("eval_config.yaml: top level must be a mapping")
            return
        timeout = cfg.get(TIMEOUT_KEY)
        if timeout is None:
            self.report.warnings.append(f"eval_config.yaml: no {TIMEOUT_KEY}, so the 12 h budget can't be checked")
            return
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            self.report.errors.append(f"eval_config.yaml: {TIMEOUT_KEY} must be a positive number, got {timeout!r}")
            return
        worst = self.tasks * (timeout + self.setup_seconds)
        line = (f"budget: {self.tasks} tasks x ({timeout:g} s + {self.setup_seconds:g} s setup) = "
                f"{worst / 3600:.1f} h of {TIME_CAP_SECONDS / 3600:g} h")
        if worst > TIME_CAP_SECONDS:
            self.report.errors.append(f"{line}: over the cap, so the whole submission would fail")
        elif worst > WARN_FRACTION * TIME_CAP_SECONDS:
            self.report.warnings.append(f"{line}: under {(1 - WARN_FRACTION):.0%} margin left")
        else:
            self.report.notes.append(line)


def _is_link(path: Path) -> bool:
    # os.path.isjunction is new in Python 3.12; Kaggle images may be older.
    isjunction = getattr(os.path, "isjunction", lambda _path: False)
    return path.is_symlink() or isjunction(path)


def _iter_includes(node: object):
    if isinstance(node, Include):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _iter_includes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_includes(value)


def _frontmatter(text: str) -> object:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None


def validate(src: Path, tasks: int = HIDDEN_TASKS, setup_seconds: float = SETUP_SECONDS_PER_TASK) -> Report:
    """Check a submission folder against the known rules. Writes nothing."""
    return _Checker(src, tasks, setup_seconds).run()


def build_zip(src: Path, files: list[Path], out: Path) -> None:
    """Write files into out, with paths relative to src, byte-for-byte reproducibly."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(src).as_posix(), date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            large = path.stat().st_size >= zipfile.ZIP64_LIMIT
            with path.open("rb") as fh, zf.open(info, "w", force_zip64=large) as dst:
                shutil.copyfileobj(fh, dst, 1024 * 1024)
    os.replace(tmp, out)


def check_zip(out: Path) -> list[str]:
    """Re-open a built archive and return any layout errors."""
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    errors = []
    if "agent.yaml" not in names:
        errors.append(f"{out.name}: agent.yaml is not at the archive root")
    unsafe = [n for n in names if n.startswith("/") or "\\" in n or ".." in PurePosixPath(n).parts]
    if unsafe:
        errors.append(f"{out.name}: unsafe entry names {unsafe}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, default=REPO_ROOT / "submission", help="folder to package")
    parser.add_argument("--out", type=Path, help="zip to write (default: dist/<name>.zip)")
    parser.add_argument("--check-only", action="store_true", help="validate without writing a zip")
    parser.add_argument("--tasks", type=int, default=HIDDEN_TASKS, help="hidden task count for the budget check")
    parser.add_argument("--setup-seconds", type=float, default=SETUP_SECONDS_PER_TASK,
                        help="sandbox setup time per task for the budget check")
    args = parser.parse_args(argv)

    src = args.src.resolve()
    if not src.is_dir():
        print(f"error: {src} is not a folder", file=sys.stderr)
        return 2
    report = validate(src, args.tasks, args.setup_seconds)
    print(f"Checking {src}")
    for label, items in (("NOTE ", report.notes), ("WARN ", report.warnings), ("ERROR", report.errors)):
        for item in items:
            print(f"  {label} {item}")
    print(f"Result: {len(report.errors)} errors, {len(report.warnings)} warnings, {len(report.files)} files")
    if report.errors:
        return 1
    if args.check_only:
        return 0

    out = (args.out or REPO_ROOT / "dist" / f"{src.name}.zip").resolve()
    build_zip(src, report.files, out)
    zip_errors = check_zip(out)
    for item in zip_errors:
        print(f"  ERROR {item}")
    if zip_errors:
        return 1
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"Wrote {out} ({out.stat().st_size / 1024:.1f} KB, sha256 {digest[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
