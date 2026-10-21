# Experiment variants

Each folder is a complete candidate submission, laid out like `submission/`, and changes one thing against its parent. Build one with `python scripts/build_submission.py --src experiments/variants/<name>`. The tests check that every folder here passes the validator.

Every config uses the harness's own format (`data/HARNESS_README.md` §2.3), checked against the official `data/sample_submission/`. Every one shares the same budget in `eval_config.yaml`: 4 min, 60 tool calls, 100 turns and 120 s per command.

| Variant | Parent | The one change | Still open |
| --- | --- | --- | --- |
| `submission/` (v0) | none | Plain prompt, 31b, nine harness tools, harness-default sampling | Ready to submit |
| `v0_12b` | v0 | Model is `gemma-4-12b-it-qat-w4a16-ct` (in `ALLOWED_MODEL_NAMES`, §3.2) | None; likely more turns per task |
| `v1_workflow` | v0 | Explicit workflow prompt: symbol-name graph lookups, `/tmp` scratch files, one targeted test, submit last | None |
| `v2_skills` | v1 | Adds the `repo_navigation` and `running_tests` skills, plus the prompt paragraph naming them | Whether `load_skill` turns pay for themselves in 4 min |
| `v3_analyzer` | v1 | Adds a read-only `code_analyzer` agent_tool, plus the prompt paragraph naming it | Its calls share the per-task budget, so compare runtime too |

Suggested order: score v0 first, then v0_12b and v1. Run v2 and v3 against v1, then combine whatever helped into a new variant. Log every run in `experiments/score_log.csv` before you merge anything into `submission/`.

The official sample can be checked as-is with `python scripts/build_submission.py --src data/sample_submission --check-only`. It's competition data, so it stays in git-ignored `data/` and never in this repo.
