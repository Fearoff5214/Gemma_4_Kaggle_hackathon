# Decision log

Newest first. Each entry gives the decision, why it was made, the alternative, and when to revisit it. Section numbers (§) refer to `data/HARNESS_README.md`.

## 2026-10-21: eval_config uses the official `evaluation:` keys

- **Decision:** `evaluation:` sets `max_time_minutes: 4`, `max_tool_calls: 60`, `max_turns: 100` and `timeout_seconds: 120`.
- **Why:**
  - 120 tasks × (4 min + about 1 min setup) = 10 h, under the 12 h cap. Without the file, the 60 min default per task would blow the cap (§7.1).
  - Time is the binding limit. The call and turn caps are backstops.
  - 120 s per command stops one slow test run from using up half the session.
  - The scorer's concurrency isn't documented, so assume tasks run one after another.
- **Alternative:** looser limits. Those would only fit if the scorer runs tasks in parallel or the hidden set is smaller than about 120 tasks.
- **Revisit:** after the first scored run reports its runtime, and once local `swegemma eval` runs show how long dev tasks really take.

## 2026-10-21: Baseline v0 uses a plain prompt; the workflow prompt is variant v1

- **Decision:** `submission/prompts/system.md` is a short plain prompt. The explicit workflow prompt (understand, locate with graph tools, reproduce, edit, verify, submit) lives in `experiments/variants/v1_workflow/`.
- **Why:** the six-week plan makes the explicit workflow the first Phase 1 change. Measuring it needs a plain baseline, and each experiment changes one thing.
- **Alternative:** submit the workflow prompt first. That likely scores higher, but then there's no number to compare it with.
- **Revisit:** after both have scores in `experiments/score_log.csv`.

## 2026-10-21: Baseline model is gemma-4-31b-it-qat-w4a16-ct

- **Decision:** the baseline uses 31b, the starter-kit default.
- **Why:** the README lists all four models in `ALLOWED_MODEL_NAMES` (§3.1, §3.2), so 12B is allowed too. That settles the conflict between the Model Selection page and the host's Oct 11 post. 31b stays the baseline as the starter-kit default.
- **Alternative:** 12B. It's reportedly about twice as fast (67 vs 35 tokens/s on 4×L4), so it gets more turns per task, and the plan prefers it for iterating. `v0_12b` is now unblocked.
- **Revisit now:** compare v0 and v0_12b locally with `swegemma eval` on the dev set before spending submissions on either.

## 2026-10-21: Agent YAML stays at the archive root, so no path needs `..`

- **Decision:** every agent YAML file lives at the archive root (the v3 analyzer is `analyzer.yaml`, not `sub_agents/…`). The validator resolves `!include`, `config_path` and skill paths from the including file's folder. It warns on `..` that stays inside the archive and rejects `..` that escapes it.
- **Why:**
  - The official `data/sample_submission/sub_agents/code_analyzer.yaml` includes `../prompts/analyzer.md`. Paths are therefore file-relative, and the harness accepts `..` inside the archive. This replaces our earlier guess that paths were root-relative.
  - The competition overview says `../` isn't allowed. With every agent at the root, no path needs it, and both readings agree.
- **Revisit:** only if a config ever needs agent files in subfolders.
