# Harness notes

**Status: written before reading the official `HARNESS_README.md`.** Everything below comes from other teams' public repos, a GitHub issue and a wiki, collected on 2026-10-21. Replace each answer with the official one once the README is downloaded. The validator constants that depend on these facts are marked `UNVERIFIED` in `scripts/build_submission.py`.

## Getting the official files

1. Each teammate joins the competition and accepts the rules: https://www.kaggle.com/competitions/gemma-4-developer-agent/rules
2. Create an API token (kaggle.com, Settings, API, Create New Token) and save it as `~/.kaggle/kaggle.json`. Never commit it; `.gitignore` already excludes it.
3. Install the CLI with `python -m pip install --user kaggle`. On Windows, `kaggle.exe` lands in `%APPDATA%\Python\Python314\Scripts`, which isn't on PATH.
4. List the files and their sizes before downloading anything large: `kaggle competitions files gemma-4-developer-agent`
5. Download the small files into `data/` (git-ignored): `kaggle competitions download -c gemma-4-developer-agent -f HARNESS_README.md -p data`

## The five questions from the handoff

| Question | Reported answer | Confidence |
| --- | --- | --- |
| How many tasks? | 129 public dev tasks from fastapi, rich, requests and httpx in `tasks.jsonl`. About 120 hidden tasks, split between the public and private leaderboards. | Reported by two teams |
| Context length? | 32,768 tokens, according to one team. | UNSURE |
| Per-task budget? | No official per-task figure. The 12 h total includes sandbox setup but not patch validation. Tasks reportedly run one after another, and going over 12 h fails the whole submission. | Reported by several teams |
| How does `eval_config.yaml` work? | Optional, with per-task limits. Reported keys: `per_task_timeout_seconds`, `max_turns_per_task`, and a context-compaction `trigger_token_estimate`. The sample config reportedly allows 1 min and 10 tool calls. | Reported, key names unconfirmed |
| Is there a local evaluation tool? | The dataset reportedly ships the `swegemma` harness (`adk-eval-core`) and a submission validator (`validate_submission` / `compile_submission`). | Reported by three teams |

## Other reported facts to confirm

- **Models.** There are four ids: `gemma-4-{31b,12b,e4b,e2b}-it-qat-w4a16-ct`. The official Model Selection page says only 31b is supported. A host post on Oct 11 (discussion 748287) says all four are allowed. The scoring script's allow-list isn't public. Every agent and sub-agent must use the same model, though adapters can differ.
- **12B serving.** The host says vLLM needs `--hf-overrides '{"architectures": ["Gemma4ForCausalLM"], "model_type": "gemma4"}'`.
- **LoRA.** The host says adapters for the smaller variants should be trained on the unquantized checkpoints from the Gemma 4 Transformers model page. An adapter folder holds `adapter_config.json` and `adapter_model.safetensors`, and an agent references it with `adapter: <name>`.
- **Speed.** One participant measured about 67 tokens/s for 12B and about 35 tokens/s for 31B on 4×L4. Nobody else has reproduced this.
- **Grading.** The harness takes the agent's `git diff --binary` and applies it with the test patch in a fresh sandbox. A task passes on exit code 0 with valid JUnit XML.
- **Harness flow.** The validator feeds a compiled ADK runner, which calls an OpenAI-compatible vLLM endpoint. Tool calls run in a Linux amd64 sandbox.
- **Tools.** There are nine harness tools. `run_skill_script` and `load_skill_resource` exist when skills are listed. **Reports conflict** on declaring them: one team declares them in `agent.yaml`, another says that fails compilation. Our validator rejects declaring them until the README settles it.
- **Skills.** A skill is a folder `skills/<name>/` with a `SKILL.md` whose frontmatter sets `name:`, plus optional `scripts/` and `resources/`. The folder is listed under the agent's `skills:` key, but the exact entry format (path or name) is unconfirmed.
- **File types.** One team lists `.yaml .yml .md .txt .py .json .safetensors` as allowed.
- **Submitting.** Upload `submission.zip` on the Submit Predictions page, or run `kaggle competitions submit -c gemma-4-developer-agent -f dist/submission.zip -m "<message>"`. Reportedly limited to 1 per day.

## Sources

- [Competition overview](https://www.kaggle.com/competitions/gemma-4-developer-agent/overview) and the [host post on new models](https://www.kaggle.com/competitions/gemma-4-developer-agent/discussion/748287)
- [gemma-agent-lab issue #24](https://github.com/jwj1342/gemma-agent-lab/issues/24): the model conflict, 12B serving, LoRA guidance and throughput
- Team repos: [gemmapilot](https://github.com/Zhiyilang/gemmapilot), [ennydev-2026](https://github.com/ennydev-2026/gemma-4-developer-agent), [rishaviitd](https://github.com/rishaviitd/kaggle.gemma.coding.agent), [M4-on-Github](https://github.com/M4-on-Github/Gemma4_Dev_Agent_Kaggle)
- [aiwiki entry](https://aiwiki.ai/wiki/gemma_4_developer_agent_competition)
