# Experiment variants

Each folder is a complete candidate submission, laid out like `submission/`, and changes one thing against its parent. Build one with `python scripts/build_submission.py --src experiments/variants/<name>`. The tests check that every folder here passes the validator.

| Variant | Parent | The one change | Blocked on |
| --- | --- | --- | --- |
| `submission/` (v0) | none | Plain prompt, 31b, nine harness tools, 240 s and 40 turns per task | Confirming field names in `HARNESS_README.md` |
| `v0_12b` | v0 | Model is `gemma-4-12b-it-qat-w4a16-ct` | Confirmation that 12B is accepted (see `docs/decisions.md`) |
| `v1_workflow` | v0 | Explicit workflow prompt: understand, locate with the graph tools first, reproduce, edit, verify, submit | Same as v0 |
| `v2_skills` | v1 | Adds the `repo_navigation` and `running_tests` skills, plus one prompt line naming them | The `skills:` entry format |
| `v3_analyzer` | v1 | Adds a read-only analyzer sub-agent (an AgentTool), plus one prompt line naming it | Same as v0; also compare runtime |

Suggested order: score v0, then v1. Run v2 and v3 against v1, then combine whatever helped into a new variant. Log every run in `experiments/score_log.csv` before you merge anything into `submission/`.
