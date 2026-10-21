"""Validate a submission folder and package it as submission.zip.

Usage:
    python scripts/build_submission.py                  # check submission/, write dist/submission.zip
    python scripts/build_submission.py --check-only     # check only, write nothing
    python scripts/build_submission.py --src experiments/variants/v1_workflow

The rules follow data/HARNESS_README.md (the dataset's harness guide, version
of 2026-10-13); section numbers are cited next to each one. Values marked
ESTIMATE are our own assumptions, not harness facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# 2.2: exactly one root config must sit at the archive root.
ROOT_CONFIG_NAMES = ("agent.yaml", "agent.yml", "root_agent.yaml", "root_agent.yml")
# 3.2: ALLOWED_MODEL_NAMES. Provider prefixes are stripped before comparing.
ALLOWED_MODELS = frozenset({
    "gemma-4-31b-it-qat-w4a16-ct",
    "gemma-4-12b-it-qat-w4a16-ct",
    "gemma-4-e4b-it-qat-w4a16-ct",
    "gemma-4-e2b-it-qat-w4a16-ct",
})
MODEL_PREFIXES = ("openai/", "google/", "hosted_vllm/", "custom/")
# 6: the nine harness tools, attached by bare name.
HARNESS_TOOLS = frozenset({
    "run_command", "submit_patch", "get_status", "read_file", "edit_file", "write_file",
    "get_code_neighbors", "search_similar_code", "get_code_subgraph",
})
# 5.3: ADK SkillToolset tools. They come with `skills:` and are not declared as tools.
SKILL_TOOLS = frozenset({"load_skill", "load_skill_resource", "run_skill_script"})
# 2.3: SandboxedAgentConfig fields per agent class.
CALLBACK_KEYS = frozenset({
    "before_agent_callbacks", "after_agent_callbacks", "before_model_callbacks",
    "after_model_callbacks", "before_tool_callbacks", "after_tool_callbacks",
})
LLM_AGENT_KEYS = frozenset({
    "agent_class", "name", "model", "adapter", "description", "instruction", "global_instruction",
    "tools", "skills", "sub_agents", "output_key", "include_contents",
    "disallow_transfer_to_parent", "disallow_transfer_to_peers", "generate_content_config",
}) | CALLBACK_KEYS
WORKFLOW_AGENT_KEYS = frozenset({"agent_class", "name", "description", "sub_agents", "max_iterations"}) | CALLBACK_KEYS
AGENT_CLASSES = ("LlmAgent", "SequentialAgent", "ParallelAgent", "LoopAgent")
# 2.4: generation constraints.
GEN_KEYS = frozenset({
    "temperature", "top_p", "top_k", "max_output_tokens", "presence_penalty", "frequency_penalty",
    "stop_sequences", "response_mime_type", "seed", "thinking_config",
})
GEN_FORBIDDEN = frozenset({"tools", "system_instruction", "http_options", "safety_settings", "response_schema"})
THINKING_LEVELS = frozenset({"MINIMAL", "LOW", "MEDIUM", "HIGH", "NONE"})
MAX_CONTEXT_TOKENS = 32_768
# 2.4: structural limits.
MAX_TOTAL_BYTES = 3_221_225_472  # total unpacked size must stay below this, adapters included
MAX_FILES = 10_000
MAX_YAML_FILES = 1_000
MAX_YAML_BYTES = 50 * 1024 * 1024
MAX_SKILL_BYTES = 50 * 1024 * 1024
MAX_SKILLS = 1_000
MAX_AGENTS = 500
MAX_INSTRUCTION_CHARS = 1_000_000
MAX_TOTAL_INSTRUCTION_CHARS = 10_000_000
MAX_INCLUDE_DEPTH = 10
MAX_LOOP_ITERATIONS = 500
MAX_LORA_RANK = 128  # 3.1
ALLOWED_SUFFIXES = frozenset({".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"})
ADAPTER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")  # 3.4
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")
# 5.1: session state the harness sets. `hints` exists only when the task has hints.
STATE_VARS = frozenset({"problem_description"})
OPTIONAL_STATE_VARS = frozenset({"hints"})
# ADK replaces {name} in instructions with session state (and raises KeyError when it's missing).
TEMPLATE = re.compile(r"\{+[^{}]*\}+")
STATE_NAME = re.compile(r"(?:(?:app|user|temp):)?[A-Za-z_][A-Za-z0-9_]*\Z")
# 7.1: eval_config.yaml keys under `evaluation:`, with the scorer's defaults.
EVAL_DEFAULTS = {"max_time_minutes": 60.0, "max_tool_calls": 100, "max_turns": 500, "timeout_seconds": 300}

# The competition's 12 h cap covers every task, sandbox setup included (setup is
# excluded from max_time_minutes, 7.1). ESTIMATE: the hidden task count, setup
# time per task, and that the scorer runs tasks one after another.
TIME_CAP_SECONDS = 12 * 3600
HIDDEN_TASKS = 120
SETUP_SECONDS_PER_TASK = 60
WARN_FRACTION = 0.9

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
        self.agent_files: set[Path] = set()
        self.include_targets: dict[tuple[Path, str], Path | None] = {}  # (folder, raw path) -> checked target
        self.include_stack: set[Path] = set()
        self.models: dict[str, str] = {}
        self.output_keys: set[str] = set()
        self.templates: list[tuple[str, str, bool]] = []  # (where, state name, optional)
        self.instruction_chars = 0
        self.agent_count = 0

    def error(self, message: str) -> None:
        self.report.errors.append(message)

    def warn(self, message: str) -> None:
        self.report.warnings.append(message)

    def note(self, message: str) -> None:
        self.report.notes.append(message)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.src).as_posix()

    def run(self) -> Report:
        self.report.files = self.walk_files()
        self.check_sizes()
        roots = [self.src / name for name in ROOT_CONFIG_NAMES if (self.src / name).is_file()]
        if not roots:
            nested = [self.rel(p) for p in self.report.files if p.name in ROOT_CONFIG_NAMES]
            found = f" (found {', '.join(nested)})" if nested else ""
            self.error(f"no root config at the archive root; expected one of {', '.join(ROOT_CONFIG_NAMES)}{found}")
        elif len(roots) > 1:
            self.error(f"more than one root config: {', '.join(self.rel(p) for p in roots)} (2.2)")
        else:
            self.check_agent_file(roots[0], is_root=True)
        self.check_standalone_models()
        self.check_models()
        self.check_templates()
        self.check_skill_dirs()
        self.check_adapter_dirs()
        self.check_budget()
        return self.report

    # Files and paths

    def walk_files(self) -> list[Path]:
        files = []
        for dirpath, dirnames, filenames in os.walk(self.src):
            here = Path(dirpath)
            for name in list(dirnames):
                if _is_link(here / name):
                    self.error(f"{self.rel(here / name)}: symlinks and junctions are not allowed")
                    dirnames.remove(name)
            for name in filenames:
                path = here / name
                if _is_link(path):
                    self.error(f"{self.rel(path)}: symlinks and junctions are not allowed")
                elif path.suffix.lower() not in ALLOWED_SUFFIXES:
                    self.error(f"{self.rel(path)}: file type not in the allow-list {sorted(ALLOWED_SUFFIXES)} (2.4)")
                else:
                    files.append(path)
        return sorted(files)

    def check_sizes(self) -> None:
        files = self.report.files
        total = sum(p.stat().st_size for p in files)
        if total >= MAX_TOTAL_BYTES:
            self.error(f"total size {total:,} bytes is not under the 3 GiB limit (2.4)")
        if len(files) > MAX_FILES:
            self.error(f"{len(files)} files is over the {MAX_FILES:,}-file limit (2.4)")
        yaml_files = [p for p in files if p.suffix.lower() in {".yaml", ".yml"}]
        if len(yaml_files) > MAX_YAML_FILES:
            self.error(f"{len(yaml_files)} YAML files is over the {MAX_YAML_FILES:,}-file limit (2.4)")
        for path in yaml_files:
            if path.stat().st_size > MAX_YAML_BYTES:
                self.error(f"{self.rel(path)}: over the 50 MiB YAML limit (2.4)")
        self.note(f"size: {len(files)} files, {total / 2**20:.2f} MiB of the 3 GiB limit")

    def safe_path(self, raw: object, where: str, base: Path | None = None) -> Path | None:
        """Return base/raw when raw is a safe relative path inside the archive; otherwise record why not.

        Paths resolve from the folder of the file that holds them: the official
        sample's sub_agents/code_analyzer.yaml includes ../prompts/analyzer.md.
        """
        if not isinstance(raw, str) or not raw.strip():
            self.error(f"{where}: path must be a non-empty string, got {raw!r}")
            return None
        if "\0" in raw or "\\" in raw:
            self.error(f"{where}: {raw!r} contains a null byte or backslash")
            return None
        pure = PurePosixPath(raw)
        if pure.is_absolute() or re.match(r"^[A-Za-z]:", raw):
            self.error(f"{where}: absolute path {raw!r} is not allowed (2.2)")
            return None
        target = Path(os.path.normpath((base or self.src).joinpath(*pure.parts)))
        try:
            target.resolve().relative_to(self.root)
        except ValueError:
            self.error(f"{where}: {raw!r} resolves outside the submission (2.2)")
            return None
        if ".." in pure.parts:
            self.warn(f"{where}: {raw!r} uses '..'. The harness accepts it inside the archive (the official "
                      f"sample does), but the competition overview says ../ isn't allowed. Keeping agent YAML at "
                      f"the archive root avoids it")
        return target

    def resolve_ref(self, raw: object, where: str, base: Path | None = None) -> Path | None:
        target = self.safe_path(raw, where, base)
        if target is not None and not target.is_file():
            self.error(f"{where}: {raw!r} not found")
            return None
        return target

    def load_yaml(self, path: Path) -> object:
        try:
            with path.open(encoding="utf-8") as fh:
                return yaml.load(fh, Loader=_SubmissionLoader)  # SafeLoader subclass
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            self.error(f"{self.rel(path)}: cannot parse YAML ({exc})")
            return None

    def check_includes(self, node: object, path: Path, depth: int = 0) -> None:
        """Check every `!include` in a loaded YAML tree, following YAML targets (2.2)."""
        rel = self.rel(path)
        if depth >= MAX_INCLUDE_DEPTH:
            self.error(f"{rel}: !include nesting is deeper than {MAX_INCLUDE_DEPTH} (2.2)")
            return
        for inc in _iter_includes(node):
            target = self.resolve_ref(inc.path, f"{rel}: !include", path.parent)
            self.include_targets[(path.parent, inc.path)] = target
            if target is None:
                continue
            suffix = target.suffix.lower()
            if suffix in {".yaml", ".yml"}:
                if target in self.include_stack:
                    self.error(f"{rel}: !include cycle through {inc.path!r}")
                    continue
                self.include_stack.add(target)
                self.check_includes(self.load_yaml(target), target, depth + 1)
                self.include_stack.discard(target)
            elif suffix not in {".md", ".txt"}:
                self.warn(f"{rel}: !include {inc.path!r}: only .md, .txt, .yaml and .yml are documented (2.2)")

    def included_value(self, value: object, base: Path) -> object:
        """What an `!include` in a file under base expands to: text for .md/.txt, parsed data for YAML."""
        if not isinstance(value, Include):
            return value
        target = self.include_targets.get((base, value.path))
        if target is None:
            return None
        if target.suffix.lower() in {".yaml", ".yml"}:
            return self.load_yaml(target)
        return target.read_text(encoding="utf-8")

    # Agents

    def check_agent_file(self, path: Path, is_root: bool = False) -> None:
        if path in self.agent_files:
            return
        self.agent_files.add(path)
        data = self.load_yaml(path)
        if data is None:
            return
        self.check_includes(data, path)
        self.check_agent(data, self.rel(path), path.parent, is_root)

    def check_agent(self, data: object, where: str, base: Path, is_root: bool = False) -> None:
        """Check one agent mapping; base is the folder of the file it came from."""
        if not isinstance(data, dict):
            self.error(f"{where}: an agent must be a mapping")
            return
        self.agent_count += 1
        if self.agent_count == MAX_AGENTS + 1:
            self.error(f"more than {MAX_AGENTS} agents (2.4)")
        agent_class = data.get("agent_class") or "LlmAgent"
        if agent_class not in AGENT_CLASSES:
            self.error(f"{where}: agent_class {agent_class!r} is not one of {', '.join(AGENT_CLASSES)} (2.3)")
            return
        allowed = LLM_AGENT_KEYS if agent_class == "LlmAgent" else WORKFLOW_AGENT_KEYS
        unknown = sorted(set(data) - allowed)
        if unknown:
            self.error(f"{where}: {unknown} are not {agent_class} fields (2.3)")
        if set(data) & CALLBACK_KEYS:
            self.warn(f"{where}: callbacks resolve against the harness's CallbackRegistry, whose names aren't documented")
        name = data.get("name")
        if not isinstance(name, str) or not name.isidentifier() or name == "user":
            self.error(f"{where}: name {name!r} must be a Python identifier other than 'user'")

        if agent_class == "LlmAgent":
            self.check_llm_agent(data, where, base, is_root)
        elif agent_class == "LoopAgent" and "max_iterations" in data:
            iterations = data["max_iterations"]
            if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= MAX_LOOP_ITERATIONS:
                self.error(f"{where}: max_iterations must be an integer from 1 to {MAX_LOOP_ITERATIONS} (2.3)")

        sub_agents = data.get("sub_agents") or []
        if not isinstance(sub_agents, list):
            self.error(f"{where}: sub_agents must be a list")
            sub_agents = []
        for i, sub in enumerate(sub_agents):
            sub_where = f"{where}: sub_agents[{i}]"
            if isinstance(sub, dict) and set(sub) == {"config_path"}:
                target = self.resolve_ref(sub["config_path"], f"{sub_where} config_path", base)
                if target is not None:
                    self.check_agent_file(target)
            else:
                self.error(f"{sub_where}: sub_agents entries are {{config_path: ...}} references (2.3)")

    def check_llm_agent(self, data: dict, where: str, base: Path, is_root: bool) -> None:
        model = data.get("model")
        if model is None:
            if is_root:
                self.error(f"{where}: the root agent must set model (2.3)")
        elif not isinstance(model, str) or _normalize_model(model) not in ALLOWED_MODELS:
            self.error(f"{where}: model {model!r} is not in ALLOWED_MODEL_NAMES {sorted(ALLOWED_MODELS)} (3.2)")
        else:
            self.models[where] = _normalize_model(model)

        for key in ("instruction", "global_instruction"):
            if key in data:
                self.check_instruction(self.included_value(data[key], base), f"{where}: {key}")
        if "instruction" not in data:
            self.warn(f"{where}: no instruction, so the agent runs on the harness prompt alone")
        if isinstance(data.get("output_key"), str):
            self.output_keys.add(data["output_key"])

        tools = data.get("tools") or []
        if not isinstance(tools, list):
            self.error(f"{where}: tools must be a list")
            tools = []
        for i, tool in enumerate(tools):
            self.check_tool(tool, f"{where}: tools[{i}]", base)

        skills = data.get("skills")
        if skills is not None and not isinstance(skills, list):
            self.error(f"{where}: skills must be a list of folder paths (2.3)")
            skills = []
        for i, skill in enumerate(skills or []):
            target = self.safe_path(skill, f"{where}: skills[{i}]", base)
            if target is not None and not (target / "SKILL.md").is_file():
                self.error(f"{where}: skills[{i}] {skill!r} is not a folder holding SKILL.md (2.3)")

        adapter = data.get("adapter")
        if adapter is not None:
            if not isinstance(adapter, str) or not ADAPTER_NAME.match(adapter):
                self.error(f"{where}: adapter {adapter!r} must match {ADAPTER_NAME.pattern} (3.4)")
            elif not ((self.src / "adapters" / adapter).is_dir()
                      or (self.src / "adapters" / f"{adapter}.safetensors").is_file()):
                self.error(f"{where}: adapter {adapter!r} not found under adapters/ (3.4)")

        if "generate_content_config" in data:
            self.check_generation(self.included_value(data["generate_content_config"], base), where)

    def check_tool(self, tool: object, where: str, base: Path) -> None:
        if isinstance(tool, str):
            if tool in SKILL_TOOLS:
                self.error(f"{where}: {tool} comes with skills:; don't declare it as a tool (5.3)")
            elif tool not in HARNESS_TOOLS:
                self.error(f"{where}: {tool!r} is not one of the nine harness tools (6)")
        elif isinstance(tool, dict) and "agent_tool" in tool:
            if set(tool) != {"agent_tool"}:
                self.error(f"{where}: an agent_tool entry takes no other keys")
            ref = tool["agent_tool"]
            if not isinstance(ref, dict) or "config_path" not in ref:
                self.error(f"{where}: agent_tool needs config_path (2.3)")
                return
            extra = sorted(set(ref) - {"config_path", "skip_summarization"})
            if extra:
                self.warn(f"{where}: agent_tool keys {extra} aren't documented (2.3)")
            target = self.resolve_ref(ref["config_path"], f"{where} agent_tool.config_path", base)
            if target is not None:
                self.check_agent_file(target)
        elif isinstance(tool, dict) and "name" in tool and set(tool) <= {"name", "args"}:
            self.error(f"{where}: plain-ADK {{name: ...}} entry; the harness reads mappings as inline agents. "
                       f"List tools by bare name instead, e.g. '- {tool['name']}' (2.3)")
        elif isinstance(tool, dict):
            self.check_agent(tool, f"{where} (inline agent)", base)
        else:
            self.error(f"{where}: a tool is a name, an agent_tool reference or an inline agent (2.3)")

    def check_instruction(self, text: object, where: str) -> None:
        if text is None:
            return  # a broken !include was already reported
        if not isinstance(text, str):
            self.error(f"{where}: must be text (inline, or !include of a .md or .txt file)")
            return
        if len(text) > MAX_INSTRUCTION_CHARS:
            self.error(f"{where}: {len(text):,} characters is over the {MAX_INSTRUCTION_CHARS:,} limit (2.4)")
        self.instruction_chars += len(text)
        if self.instruction_chars > MAX_TOTAL_INSTRUCTION_CHARS:
            self.error(f"instructions total more than {MAX_TOTAL_INSTRUCTION_CHARS:,} characters (2.4)")
        for match in TEMPLATE.finditer(text):
            name = match.group().strip("{}").strip()
            optional = name.endswith("?")
            name = name.rstrip("?")
            if STATE_NAME.match(name):  # ADK leaves anything else in braces as literal text
                self.templates.append((where, name, optional))

    def check_templates(self) -> None:
        for where, name, optional in self.templates:
            if optional or name in STATE_VARS:
                continue
            if name in OPTIONAL_STATE_VARS:
                self.error(f"{where}: write {{{name}?}}, not {{{name}}}: the harness sets {name} only when "
                           f"the task has some, and ADK raises KeyError otherwise (5.1)")
            elif name in self.output_keys:
                self.warn(f"{where}: {{{name}}} is set only after the agent with output_key {name!r} has run")
            else:
                self.error(f"{where}: {{{name}}} is not session state the harness sets, so ADK raises KeyError. "
                           f"Remove the braces if it's meant as plain text (5.1)")

    def check_generation(self, gen: object, where: str) -> None:
        where = f"{where}: generate_content_config"
        if gen is None:
            return
        if not isinstance(gen, dict):
            self.error(f"{where}: must be a mapping")
            return
        forbidden = sorted(set(gen) & GEN_FORBIDDEN)
        if forbidden:
            self.error(f"{where}: {forbidden} are not allowed (2.4)")
        unknown = sorted(set(gen) - GEN_KEYS - GEN_FORBIDDEN)
        if unknown:
            self.warn(f"{where}: {unknown} aren't listed in the generation constraints (2.4)")
        if "max_output_tokens" in gen and not _int_in(gen["max_output_tokens"], 1, MAX_CONTEXT_TOKENS):
            self.error(f"{where}: max_output_tokens must be an integer from 1 to {MAX_CONTEXT_TOKENS} (2.4)")
        if "temperature" in gen and not (_number(gen["temperature"]) and gen["temperature"] >= 0):
            self.error(f"{where}: temperature must be a number >= 0 (2.4)")
        if "top_p" in gen and not (_number(gen["top_p"]) and 0 <= gen["top_p"] <= 1):
            self.error(f"{where}: top_p must be between 0 and 1 (2.4)")
        if "top_k" in gen and not _int_in(gen["top_k"], 1, None):
            self.error(f"{where}: top_k must be an integer >= 1 (2.4)")
        thinking = gen.get("thinking_config")
        if thinking is None:
            return
        if not isinstance(thinking, dict):
            self.error(f"{where}: thinking_config must be a mapping")
            return
        if "thinking_budget" in thinking and not _int_in(thinking["thinking_budget"], 0, MAX_CONTEXT_TOKENS):
            self.error(f"{where}: thinking_budget must be an integer from 0 to {MAX_CONTEXT_TOKENS} (2.4)")
        level = thinking.get("thinking_level")
        if level is not None and str(level).upper() not in THINKING_LEVELS:
            self.error(f"{where}: thinking_level must be one of {sorted(THINKING_LEVELS)} (2.4)")
        if level is not None:
            self.warn(f"{where}: the README advises thinking_budget + include_thoughts instead of "
                      f"thinking_level when serving through vLLM (2.4)")
        if "include_thoughts" in thinking and not isinstance(thinking["include_thoughts"], bool):
            self.error(f"{where}: include_thoughts must be true or false (2.4)")

    def check_standalone_models(self) -> None:
        """3.2: the model check also reads standalone agent YAML files anywhere in the archive."""
        for path in self.report.files:
            if path.suffix.lower() not in {".yaml", ".yml"} or path.name == "eval_config.yaml":
                continue
            if path in self.agent_files:
                continue
            data = self.load_yaml(path)
            if isinstance(data, dict) and isinstance(data.get("model"), str):
                self.models[self.rel(path)] = _normalize_model(data["model"])
                if self.models[self.rel(path)] not in ALLOWED_MODELS:
                    self.error(f"{self.rel(path)}: model {data['model']!r} is not in ALLOWED_MODEL_NAMES (3.2)")

    def check_models(self) -> None:
        distinct = set(self.models.values())
        if not distinct and not self.report.errors:
            self.error("no agent declares a model (3.2)")
        if len(distinct) > 1:
            listing = ", ".join(f"{where}: {model}" for where, model in sorted(self.models.items()))
            self.error(f"every agent must use the same model ({listing}) (3.2)")

    # Folders

    def check_skill_dirs(self) -> None:
        skills_dir = self.src / "skills"
        if not skills_dir.is_dir():
            return
        skills = sorted(p for p in skills_dir.iterdir() if p.is_dir())
        if len(skills) > MAX_SKILLS:
            self.error(f"{len(skills)} skills is over the {MAX_SKILLS:,} limit (2.4)")
        for skill in skills:
            rel = self.rel(skill)
            size = sum(p.stat().st_size for p in skill.rglob("*") if p.is_file())
            if size > MAX_SKILL_BYTES:
                self.error(f"{rel}: {size:,} bytes is over the 50 MiB per-skill limit (2.4)")
            skill_md = skill / "SKILL.md"
            if not skill_md.is_file():
                self.error(f"{rel}: missing SKILL.md")
                continue
            meta = _frontmatter(skill_md.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or not meta.get("name"):
                self.error(f"{rel}/SKILL.md: frontmatter must set name")
            elif not meta.get("description"):
                self.warn(f"{rel}/SKILL.md: frontmatter has no description, so the model can't tell when to use it")

    def check_adapter_dirs(self) -> None:
        adapters_dir = self.src / "adapters"
        if not adapters_dir.is_dir():
            return
        for adapter in sorted(p for p in adapters_dir.iterdir() if p.is_dir()):
            rel = self.rel(adapter)
            if not ADAPTER_NAME.match(adapter.name):
                self.error(f"{rel}: adapter folder name must match {ADAPTER_NAME.pattern} (3.4)")
            for needed in ADAPTER_FILES:
                if not (adapter / needed).is_file():
                    self.error(f"{rel}: missing {needed} (3.4)")
            config = adapter / "adapter_config.json"
            if config.is_file():
                try:
                    rank = json.loads(config.read_text(encoding="utf-8")).get("r")
                except (ValueError, AttributeError):
                    self.error(f"{rel}/adapter_config.json: not a JSON object")
                    continue
                if isinstance(rank, int) and rank > MAX_LORA_RANK:
                    self.error(f"{rel}: LoRA rank {rank} is over the max_lora_rank of {MAX_LORA_RANK} (3.1)")

    # Budget

    def check_budget(self) -> None:
        if (self.src / "eval_config.yml").is_file():
            self.warn("eval_config.yml: only eval_config.yaml is documented (7.1)")
        cfg_path = self.src / "eval_config.yaml"
        budget = dict(EVAL_DEFAULTS)
        source = "scorer defaults"
        if cfg_path.is_file():
            cfg = self.load_yaml(cfg_path)
            if cfg is None:
                return
            if not isinstance(cfg, dict) or not isinstance(cfg.get("evaluation"), dict):
                self.error("eval_config.yaml: budgets go under an `evaluation:` mapping (7.1)")
                return
            if set(cfg) - {"evaluation"}:
                self.warn(f"eval_config.yaml: {sorted(set(cfg) - {'evaluation'})} are not documented (7.1)")
            for key, value in cfg["evaluation"].items():
                if key not in EVAL_DEFAULTS:
                    self.error(f"eval_config.yaml: evaluation.{key} is not a budget key; "
                               f"use {', '.join(EVAL_DEFAULTS)} (7.1)")
                elif not _number(value) or value <= 0:
                    self.error(f"eval_config.yaml: evaluation.{key} must be a positive number, got {value!r}")
                else:
                    budget[key] = value
            source = "eval_config.yaml"
        minutes = budget["max_time_minutes"]
        worst = self.tasks * (minutes * 60 + self.setup_seconds)
        line = (f"budget ({source}): {self.tasks} tasks x ({minutes:g} min + {self.setup_seconds:g} s setup) = "
                f"{worst / 3600:.1f} h of {TIME_CAP_SECONDS / 3600:g} h")
        if worst > TIME_CAP_SECONDS:
            self.error(f"{line}: over the cap if tasks use their full time, which fails the whole "
                       f"submission. Lower evaluation.max_time_minutes")
        elif worst > WARN_FRACTION * TIME_CAP_SECONDS:
            self.warn(f"{line}: under {1 - WARN_FRACTION:.0%} margin left")
        else:
            self.note(line)
        self.note(f"per task: {budget['max_tool_calls']:g} tool calls, {budget['max_turns']:g} turns, "
                  f"{budget['timeout_seconds']:g} s per command")


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


def _normalize_model(model: str) -> str:
    for prefix in MODEL_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int_in(value: object, low: int, high: int | None) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= low and (high is None or value <= high))


def _frontmatter(text: str) -> object:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None


def validate(src: Path, tasks: int = HIDDEN_TASKS, setup_seconds: float = SETUP_SECONDS_PER_TASK) -> Report:
    """Check a submission folder against the harness rules. Writes nothing."""
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
    if sum(name in ROOT_CONFIG_NAMES for name in names) != 1:
        errors.append(f"{out.name}: needs exactly one root config at the archive root")
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
