# Decision log

Newest first. Each entry gives the decision, why it was made, the alternative, and when to revisit it.

## 2026-10-21: Baseline v0 uses a plain prompt; the workflow prompt is variant v1

- **Decision:** `submission/prompts/system.md` is a short plain prompt. The explicit workflow prompt (understand, locate with graph tools, reproduce, edit, verify, submit) lives in `experiments/variants/v1_workflow/`.
- **Why:** the six-week plan makes the explicit workflow the first Phase 1 change. Measuring it needs a plain baseline, and each experiment changes one thing.
- **Alternative:** submit the workflow prompt first. That likely scores higher, but then there's no number to compare it with.
- **Revisit:** after both have scores in `experiments/score_log.csv`.

## 2026-10-21: Baseline model is gemma-4-31b-it-qat-w4a16-ct

- **Decision:** every agent uses 31b until the allowed-model question is settled.
- **Why:** 31b is the only model every official source allows. The Model Selection page says 31b only, the host's Oct 11 post allows all four, and the scoring script's allow-list isn't public. A rejected baseline would waste a day's submission.
- **Alternative:** 12B. It's reportedly about twice as fast (67 vs 35 tokens/s on 4×L4), so it gets more turns per task, and the plan prefers it for iterating.
- **Revisit:** when `HARNESS_README.md` or the host confirms 12B. Then run the same config on 12B as a one-change experiment.

## 2026-10-21: Per-task limits are 240 s and 40 turns

- **Decision:** `per_task_timeout_seconds: 240` and `max_turns_per_task: 40`.
- **Why:** 120 tasks × (240 s + about 60 s of setup) = 10 h, under the 12 h cap. Going over the cap fails the whole submission.
- **Alternative:** the harness defaults, which are unknown, or the sample's 1 min, which is too short to fix anything.
- **Revisit:** once the README confirms the task count, the setup time and the key names, and again after the first run reports its real runtime.

## 2026-10-21: `!include` paths are resolved from the archive root, and agent YAML stays at the root

- **Decision:** the validator resolves `!include` from the archive root and warns about includes in files below the root. All agent YAML files live at the archive root.
- **Why:** the rules ban `..`. That only makes sense if paths never need to climb out of a subfolder, which suggests root-relative paths. With every agent at the root, root-relative and file-relative resolution give the same answer.
- **Revisit:** when the README states how includes are resolved.
