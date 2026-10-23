"""Tests for scripts/build_submission.py. Run: python -m unittest discover -s tests

Section numbers refer to data/HARNESS_README.md.
"""

import json
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
      - run_command
      - submit_patch
""")

ANALYZER_TOOL = (
    "  - agent_tool:\n"
    "      config_path: sub_agents/analyzer.yaml\n"
    "      skip_summarization: true\n"
)

ANALYZER = textwrap.dedent("""\
    name: analyzer
    model: gemma-4-31b-it-qat-w4a16-ct
    instruction: !include ../prompts/system.md
    tools:
      - read_file
""")


class FixtureMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "submission"
        self.write("agent.yaml", BASE_AGENT)
        self.write("prompts/system.md", "Fix the issue, then call submit_patch.\n")
        self.write("eval_config.yaml", "evaluation:\n  max_time_minutes: 4\n")

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

    def assert_clean(self):
        self.assertEqual(bs.validate(self.src).errors, [])


class LayoutTests(FixtureMixin, unittest.TestCase):
    def test_minimal_submission_passes(self):
        self.assert_clean()

    def test_missing_root_config_rejected(self):
        (self.src / "agent.yaml").rename(self.write("nested/x.md", "").with_name("agent.yaml"))
        self.assert_error("no root config at the archive root")

    def test_two_root_configs_rejected(self):
        self.write("root_agent.yaml", BASE_AGENT)
        self.assert_error("more than one root config")

    def test_symlink_rejected(self):
        try:
            os.symlink(self.src / "prompts" / "system.md", self.src / "prompts" / "link.md")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlinks here: {exc}")
        self.assert_error("symlinks and junctions are not allowed")

    def test_disallowed_file_type_rejected(self):
        self.write("run.sh", "echo hi\n")
        self.assert_error("file type not in the allow-list")


class IncludeTests(FixtureMixin, unittest.TestCase):
    def test_absolute_include_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "/etc/passwd"))
        self.assert_error("absolute path")

    def test_windows_drive_include_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "C:/prompts/system.md"))
        self.assert_error("absolute path")

    def test_include_escaping_archive_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "../system.md"))
        self.assert_error("resolves outside the submission")

    def test_missing_include_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "prompts/missing.md"))
        self.assert_error("not found")

    def test_include_is_relative_to_the_including_file(self):
        # The official sample's sub_agents/code_analyzer.yaml includes ../prompts/analyzer.md.
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("sub_agents/analyzer.yaml", ANALYZER)
        report = bs.validate(self.src)
        self.assertEqual(report.errors, [])
        self.assertTrue(any("uses '..'" in w for w in report.warnings), report.warnings)

    def test_root_relative_include_from_subfolder_rejected(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("sub_agents/analyzer.yaml", ANALYZER.replace("../prompts/system.md", "prompts/system.md"))
        self.assert_error("not found")


class AgentTests(FixtureMixin, unittest.TestCase):
    def test_unknown_model_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("gemma-4-31b-it-qat-w4a16-ct", "gemma-3-27b-it"))
        self.assert_error("not in ALLOWED_MODEL_NAMES")

    def test_provider_prefix_is_stripped(self):
        self.write("agent.yaml", BASE_AGENT.replace("model: ", "model: openai/"))
        self.assert_clean()

    def test_mixed_models_rejected(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("sub_agents/analyzer.yaml", ANALYZER.replace("31b", "12b"))
        self.assert_error("every agent must use the same model")

    def test_standalone_yaml_model_counts(self):
        self.write("configs/other_agent.yaml", "name: other\nmodel: gemma-4-12b-it-qat-w4a16-ct\n")
        self.assert_error("every agent must use the same model")

    def test_unknown_agent_key_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("instruction:", "instructions:"))
        self.assert_error("are not LlmAgent fields")

    def test_invalid_agent_name_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("name: swe_agent", "name: swe-agent"))
        self.assert_error("must be a Python identifier")

    def test_unknown_agent_class_rejected(self):
        self.write("agent.yaml", "agent_class: CustomAgent\n" + BASE_AGENT)
        self.assert_error("agent_class 'CustomAgent'")

    def test_code_reference_sub_agent_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "sub_agents:\n  - code: my_pkg.agent\n")
        self.assert_error("sub_agents entries are")


class ToolTests(FixtureMixin, unittest.TestCase):
    def test_agent_tool_sub_agent_passes(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("sub_agents/analyzer.yaml", ANALYZER)
        self.assert_clean()

    def test_adk_style_tool_mapping_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("- run_command", "- name: run_command"))
        self.assert_error("the harness reads mappings as inline agents")

    def test_non_harness_tool_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "  - google_search\n")
        self.assert_error("is not one of the nine harness tools")

    def test_declared_skill_tool_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "  - run_skill_script\n")
        self.assert_error("comes with skills:")

    def test_agent_tool_without_config_path_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "  - agent_tool:\n      skip_summarization: true\n")
        self.assert_error("agent_tool needs config_path")


class InstructionTests(FixtureMixin, unittest.TestCase):
    def test_problem_description_template_passes(self):
        self.write("prompts/system.md", "Issue: {problem_description}\n")
        self.assert_clean()

    def test_hints_without_question_mark_rejected(self):
        self.write("prompts/system.md", "Hints: {hints}\n")
        self.assert_error("write {hints?}")

    def test_optional_hints_pass(self):
        self.write("prompts/system.md", "Hints: {hints?}\n")
        self.assert_clean()

    def test_unknown_template_variable_rejected(self):
        self.write("prompts/system.md", "Return a dict like {result}.\n")
        self.assert_error("is not session state the harness sets")

    def test_non_identifier_braces_are_literal(self):
        self.write("prompts/system.md", 'JSON looks like {"a": 1}.\n')
        self.assert_clean()


class GenerationTests(FixtureMixin, unittest.TestCase):
    def test_sampling_include_passes(self):
        self.write("agent.yaml", BASE_AGENT + "generate_content_config: !include configs/sampling.yaml\n")
        self.write("configs/sampling.yaml", textwrap.dedent("""\
            temperature: 0.2
            max_output_tokens: 16384
            thinking_config:
              thinking_budget: 4096
              include_thoughts: true
        """))
        self.assert_clean()

    def test_forbidden_generation_field_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "generate_content_config:\n  safety_settings: []\n")
        self.assert_error("['safety_settings'] are not allowed")

    def test_max_output_tokens_over_context_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "generate_content_config:\n  max_output_tokens: 40000\n")
        self.assert_error("max_output_tokens must be an integer from 1 to 32768")

    def test_thinking_budget_over_context_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "generate_content_config:\n  thinking_config:\n    thinking_budget: 50000\n")
        self.assert_error("thinking_budget must be an integer")


class FolderTests(FixtureMixin, unittest.TestCase):
    def test_skill_without_skill_md_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "skills:\n  - skills/nav\n")
        self.write("skills/nav/README.md", "no SKILL.md here\n")
        self.assert_error("is not a folder holding SKILL.md")

    def test_skill_without_name_rejected(self):
        self.write("skills/nav/SKILL.md", "---\ndescription: no name here\n---\nBody\n")
        self.assert_error("frontmatter must set name")

    def test_listed_skill_passes(self):
        self.write("agent.yaml", BASE_AGENT + "skills:\n  - skills/nav\n")
        self.write("skills/nav/SKILL.md", "---\nname: nav\ndescription: How to navigate.\n---\nBody\n")
        self.assert_clean()

    def test_adapter_missing_weights_rejected(self):
        self.write("adapters/lora_a/adapter_config.json", "{}")
        self.assert_error("missing adapter_model.safetensors")

    def test_adapter_rank_over_limit_rejected(self):
        self.write("adapters/lora_a/adapter_config.json", json.dumps({"r": 256}))
        self.write("adapters/lora_a/adapter_model.safetensors", "x")
        self.assert_error("LoRA rank 256 is over the max_lora_rank of 128")

    def test_unknown_adapter_reference_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "adapter: main_lora\n")
        self.assert_error("adapter 'main_lora' not found under adapters/")


class BudgetTests(FixtureMixin, unittest.TestCase):
    def test_budget_note_when_within_cap(self):
        notes = bs.validate(self.src).notes
        self.assertTrue(any("10.0 h of 12 h" in n for n in notes), notes)

    def test_budget_over_cap_rejected(self):
        self.write("eval_config.yaml", "evaluation:\n  max_time_minutes: 6\n")
        self.assert_error("over the cap")

    def test_missing_eval_config_rejected(self):
        (self.src / "eval_config.yaml").unlink()
        self.assert_error("budget (scorer defaults)")

    def test_top_level_budget_keys_rejected(self):
        self.write("eval_config.yaml", "per_task_timeout_seconds: 240\n")
        self.assert_error("budgets go under an `evaluation:` mapping")

    def test_misspelled_budget_key_rejected(self):
        self.write("eval_config.yaml", "evaluation:\n  max_time_minutes: 4\n  max_minutes: 4\n")
        self.assert_error("evaluation.max_minutes is not a budget key")


class ReviewRegressionTests(FixtureMixin, unittest.TestCase):
    """Holes found in the 2026-10-21 review; the scorer runs on case-sensitive Linux."""

    def test_wrong_case_include_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "Prompts/SYSTEM.md"))
        self.assert_error("not found")

    def test_trailing_dot_folder_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "prompts./system.md"))
        self.assert_error("not found")

    def test_wrong_case_eval_config_rejected(self):
        (self.src / "eval_config.yaml").rename(self.src / "EVAL_CONFIG.yaml")
        self.assert_error("only reads eval_config.yaml")

    def test_wrong_case_skill_md_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "skills:\n  - skills/nav\n")
        self.write("skills/nav/skill.md", "---\nname: nav\n---\n")
        self.assert_error("is not a folder holding SKILL.md")

    def test_empty_eval_config_uses_defaults_and_fails_budget(self):
        self.write("eval_config.yaml", "# nothing yet\n")
        self.assert_error("over the cap")

    def test_empty_sub_agent_file_rejected(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("sub_agents/analyzer.yaml", "# TODO\n")
        self.assert_error("agent file is empty")

    def test_reentry_through_folder_name_rejected(self):
        self.write("agent.yaml", BASE_AGENT.replace("prompts/system.md", "../submission/prompts/system.md"))
        self.assert_error("resolves outside the submission")

    def test_nan_budget_rejected(self):
        self.write("eval_config.yaml", "evaluation:\n  max_time_minutes: .nan\n")
        self.assert_error("must be a positive number")

    def test_fractional_tool_calls_rejected(self):
        self.write("eval_config.yaml", "evaluation:\n  max_time_minutes: 4\n  max_tool_calls: 10.5\n")
        self.assert_error("must be a positive integer")

    def test_config_path_cycle_rejected(self):
        self.write("agent.yaml", BASE_AGENT + ANALYZER_TOOL)
        self.write("sub_agents/analyzer.yaml", ANALYZER + "  - agent_tool:\n      config_path: ../agent.yaml\n")
        self.assert_error("config_path cycle")

    def test_generation_typo_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "generate_content_config:\n  max_ouput_tokens: 4096\n")
        self.assert_error("aren't generation fields")

    def test_thinking_typo_rejected(self):
        self.write("agent.yaml", BASE_AGENT + "generate_content_config:\n  thinking_config:\n    thinking_budjet: 9\n")
        self.assert_error("aren't thinking_config fields")

    def test_mixed_type_keys_do_not_crash(self):
        self.write("agent.yaml", BASE_AGENT + "on: x\ninstructions: y\n")
        self.assert_error("are not LlmAgent fields")

    def test_non_utf8_prompt_reported(self):
        (self.src / "prompts" / "system.md").write_bytes("caf\xe9\n".encode("cp1252"))
        self.assert_error("cannot read as UTF-8")

    def test_recursive_alias_does_not_crash(self):
        self.write("agent.yaml", BASE_AGENT + "description: &a [*a]\n")
        bs.validate(self.src)  # must return, not raise RecursionError

    def test_tools_mapping_rejected(self):
        self.write("agent.yaml", BASE_AGENT.split("tools:")[0] + "tools: {}\n")
        self.assert_error("tools must be a list")

    def test_frontmatter_without_trailing_newline_passes(self):
        self.write("agent.yaml", BASE_AGENT + "skills:\n  - skills/nav\n")
        self.write("skills/nav/SKILL.md", "---\nname: nav\ndescription: d\n---")
        self.assert_clean()


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
