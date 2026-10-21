"""Tests for scripts/build_submission.py. Run: python -m unittest discover -s tests"""

import os
import shutil
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_submission as bs  # noqa: E402

BASE_AGENT = textwrap.dedent("""\
    name: swe_agent
    model: gemma-4-31b-it-qat-w4a16-ct
    instruction: !include prompts/system.md
    tools:
      - name: run_command
      - name: submit_patch
""")

# Appended under BASE_AGENT's tools list, so it keeps that list's indentation.
ANALYZER_TOOL = (
    "  - name: AgentTool\n"
    "    args:\n"
    "      agent:\n"
    "        config_path: analyzer.yaml\n"
)


class FixtureMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "submission"
        self.write("agent.yaml", BASE_AGENT)
        self.write("prompts/system.md", "Fix the issue, then call submit_patch.\n")
        self.write("eval_config.yaml", "per_task_timeout_seconds: 240\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        path = self.src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def assert_error(self, fragment):
        errors = bs.validate(self.src).errors
        self.assertTrue(any(fragment in e for e in errors), f"no error containing {fragment!r} in {errors}")


class ValidateTests(FixtureMixin, unittest.TestCase):
    def test_minimal_submission_passes(self):
        self.assertEqual(bs.validate(self.src).errors, [])

    def test_include_with_parent_dir_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "../system.md"))
        self.assert_error("'..' is not allowed")

    def test_absolute_include_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "/etc/passwd"))
        self.assert_error("absolute path")

    def test_windows_drive_include_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "C:/prompts/system.md"))
        self.assert_error("absolute path")

    def test_missing_include_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "prompts/missing.md"))
        self.assert_error("not found")

    def test_symlink_rejected(self):
        try:
            os.symlink(self.src / "prompts" / "system.md", self.src / "prompts" / "link.md")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlinks here: {exc}")
        self.assert_error("symlinks and junctions are not allowed")

    def test_unknown_model_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("gemma-4-31b-it-qat-w4a16-ct", "gemma-3-27b-it"))
        self.assert_error("not an allowed model")

    def test_mixed_models_rejected(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("analyzer.yaml", BASE_AGENT.replace("31b", "12b").replace("swe_agent", "analyzer"))
        self.assert_error("every agent must use the same model")

    def test_agent_tool_sub_agent_passes(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("analyzer.yaml", BASE_AGENT.replace("swe_agent", "analyzer"))
        self.assertEqual(bs.validate(self.src).errors, [])

    def test_agent_tool_outside_archive_rejected(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL.replace("analyzer.yaml", "../analyzer.yaml"))
        self.assert_error("'..' is not allowed")

    def test_non_harness_tool_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "  - name: google_search\n")
        self.assert_error("is not a harness tool")

    def test_declared_skill_tool_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "  - name: run_skill_script\n")
        self.assert_error("added by the harness")

    def test_agent_yaml_not_at_root_rejected(self):
        (self.src / "agent.yaml").rename(self.write("nested/x.md", "").with_name("agent.yaml"))
        self.assert_error("agent.yaml must be at the archive root (found nested/agent.yaml)")

    def test_budget_over_cap_rejected(self):
        self.write("eval_config.yaml", "per_task_timeout_seconds: 400\n")
        self.assert_error("over the cap")

    def test_budget_note_when_within_cap(self):
        notes = bs.validate(self.src).notes
        self.assertTrue(any("10.0 h of 12 h" in n for n in notes), notes)

    def test_skill_without_name_rejected(self):
        self.write("skills/nav/SKILL.md", "---\ndescription: no name here\n---\nBody\n")
        self.assert_error("frontmatter must set name")

    def test_adapter_missing_weights_rejected(self):
        self.write("adapters/lora_a/adapter_config.json", "{}")
        self.assert_error("missing adapter_model.safetensors")

    def test_disallowed_file_type_rejected(self):
        self.write("run.sh", "echo hi\n")
        self.assert_error("file type not in the allow-list")


class BuildTests(FixtureMixin, unittest.TestCase):
    def test_zip_has_agent_yaml_at_root(self):
        out = self.tmp / "out" / "submission.zip"
        bs.build_zip(self.src, bs.validate(self.src).files, out)
        with zipfile.ZipFile(out) as zf:
            self.assertIn("agent.yaml", zf.namelist())
            self.assertIn("prompts/system.md", zf.namelist())
        self.assertEqual(bs.check_zip(out), [])

    def test_build_is_reproducible(self):
        files = bs.validate(self.src).files
        first, second = self.tmp / "a.zip", self.tmp / "b.zip"
        bs.build_zip(self.src, files, first)
        bs.build_zip(self.src, files, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())


class RepoSubmissionTests(unittest.TestCase):
    """The configs committed in this repo must always pass."""

    def test_baseline_submission_is_valid(self):
        self.assertEqual(bs.validate(REPO_ROOT / "submission").errors, [])

    def test_variants_are_valid(self):
        variants = REPO_ROOT / "experiments" / "variants"
        for variant in sorted(p for p in variants.glob("*") if p.is_dir()):
            with self.subTest(variant=variant.name):
                self.assertEqual(bs.validate(variant).errors, [])


if __name__ == "__main__":
    unittest.main()
