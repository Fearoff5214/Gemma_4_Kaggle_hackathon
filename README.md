# Gemma 4 Developer Agent Competition

This is the team repo for the [Google - Gemma 4 Developer Agent Competition](https://www.kaggle.com/competitions/gemma-4-developer-agent) on Kaggle. The job is to post-train one Gemma 4 variant into an agent that fixes real repository issues. The score is the share of patched repos that pass their validation tests, as in SWE-bench. The final submission is due **December 2, 2026 at 11:59 PM UTC** (5:29 AM IST on December 3).

**Status, 2026-10-21:** Phase 0 (setup and baseline). A baseline config is in `submission/`, but it hasn't been checked against `HARNESS_README.md` yet and hasn't been submitted. The full plan is in the [six-week plan doc](https://claude.ai/artifact/A9usjMjJLXXoXBT4nXpFax).

## Layout

| Path | What it holds |
| --- | --- |
| `submission/` | Everything that goes into `submission.zip`. `agent.yaml` must sit at its root. |
| `scripts/build_submission.py` | Validates `submission/` and writes `dist/submission.zip` |
| `tests/` | Tests for the build and validation script |
| `experiments/variants/` | Candidate configs, one folder per experiment, laid out like `submission/` |
| `experiments/score_log.csv` | One row per scored run or submission |
| `docs/harness_notes.md` | What `HARNESS_README.md` says: task count, context length, budgets, local eval |
| `docs/decisions.md` | Dated decision log |
| `data/` | The competition download. Git-ignored, never committed. |
| `dist/` | Build output. Git-ignored. |

## Setup

You need Python 3.10 or newer, PyYAML, and the Kaggle CLI: `python -m pip install pyyaml kaggle`.

1. Join the competition and accept its rules on Kaggle. Every teammate does this.
2. Create an API token on Kaggle (Settings, then API, then Create New Token) and save it as `~/.kaggle/kaggle.json`. Or set `KAGGLE_USERNAME` and `KAGGLE_KEY` from `.env.example`. Never commit either one.
3. Download the competition files into `data/`:

   ```
   kaggle competitions files -c gemma-4-developer-agent
   kaggle competitions download -c gemma-4-developer-agent -p data
   ```

## Build and validate

```
python scripts/build_submission.py
python -m unittest discover -s tests
```

The first command checks `submission/` against the rules we know about and writes `dist/submission.zip`. The second runs the tests.

To build an experiment variant instead of the baseline, point the script at that variant's folder:

```
python scripts/build_submission.py --src experiments/variants/<name>
```

## Submit

A submission spends the team's daily quota (reportedly one per day, not yet confirmed) and takes hours to score. Only the submission owner submits, and only once the team agrees:

```
kaggle competitions submit -c gemma-4-developer-agent -f dist/submission.zip -m "<experiment>: <one-line change>"
```

Then add a row to `experiments/score_log.csv` with `score` set to `pending`, and fill it in once the score comes back.

## Working rules

- `main` holds the last known-good config.
- Each experiment gets its own branch, `exp/<initials>-<change>` (for example `exp/lp-graph-first-prompt`), and changes one thing.
- Log every scored run in `experiments/score_log.csv` as one row: what changed, the model, the score and the date. Merge to `main` only when the log shows a gain.
- Tag every commit you submit as `sub-YYYYMMDD-N`, for example `sub-20261027-1`.
- One person owns the final `submission.zip`. The other two accounts merge into that team before **November 25**, the entry and team-merger deadline.
- Everyone moves onto final checks in the last week.

## Team

| Role | Owns | Person |
| --- | --- | --- |
| A: Harness lead | `agent.yaml`, system prompt, workflow design, sub-agent | TBD |
| B: Tools and skills | Graph-tool use, skills, per-task time limits | TBD |
| C: Eval and LoRA | Baseline run, score log, trajectory collection and fine-tuning if the go/no-go says yes | TBD |

Submission owner: TBD.

## Rules digest

The rules below come from the [competition overview](https://www.kaggle.com/competitions/gemma-4-developer-agent/overview), as summarised in the team handoff. Anything marked **unconfirmed** comes from other teams' public notes and still needs checking against `data/HARNESS_README.md`.

- **One base model for every agent and sub-agent.** The choices are `gemma-4-31b-it-qat-w4a16-ct`, `gemma-4-12b-it-qat-w4a16-ct`, `gemma-4-e4b-it-qat-w4a16-ct` and `gemma-4-e2b-it-qat-w4a16-ct`, all QAT w4a16. Each agent can use a different LoRA adapter. **Unconfirmed:** the official Model Selection page still lists only 31b, while a host announcement on October 11 allows all four ([summary of both](https://github.com/jwj1342/gemma-agent-lab/issues/24)).
- **Submission format.** A `submission.zip` with `agent.yaml` (a Google ADK Agent Config) at its root. Prompts, sub-agents, skills and PEFT LoRA adapters are optional.
- **Nothing outside the archive.** `!include` paths must stay inside the zip. `../` and symlinks are not allowed.
- **Harness tools only.** The agent may use `run_command`, `read_file`, `edit_file`, `write_file`, `submit_patch` and `get_status`, plus three code-graph tools: `get_code_neighbors`, `search_similar_code` and `get_code_subgraph`.
- **Time budget.** 12 hours for all tasks, including sandbox setup but not patch validation. Per-task limits go in `eval_config.yaml`. **Unconfirmed:** tasks run one after another, and going over 12 hours fails the whole submission.
- **Compute.** Kaggle L4x4 (96 GB of GPU memory), only in notebooks attached to the competition, with the internet off. These use GPU quota at twice the normal rate.
- **Tasks. Unconfirmed:** a public dev set of 129 tasks (fastapi, rich, requests, httpx) and a hidden test set of about 120 tasks from private Python repos.

## Key dates

All deadlines are at 11:59 PM UTC.

| Date | What |
| --- | --- |
| Nov 12 | Optional paper-track deadline (the plan says skip it) |
| Nov 25 | Entry and team-merger deadline; the team must be final |
| Dec 2 | Final submission deadline |

## Known limits

- Until someone reads `data/HARNESS_README.md`, the config in `submission/` and the validation rules are based on the ADK schema and other teams' notes. Treat both as unconfirmed.
- The build script checks only the rules we know about. It doesn't run the agent and can't predict a score.
- Adapter weights (`*.safetensors`) are git-ignored, so share them another way, such as a private Kaggle dataset.
