# Harness notes

These are the official facts from `data/HARNESS_README.md` (the dataset version published 2026-10-13), checked on 2026-10-21. Numbers in brackets (§) are that README's section numbers. The table near the end lists what our pre-README notes got wrong.

## The five questions from the handoff

| Question | Official answer | § |
| --- | --- | --- |
| How many tasks? | The README doesn't give the hidden task count. The dev set is `tasks.jsonl`; see `docs/dataset_inventory.md` for counts per repo. | 9.1 |
| Context length? | 32,768 tokens (vLLM `max_model_len`), shared by prompt, reasoning and output. | 2.4, 3.1 |
| Per-task budget? | Defaults: 60 min of agent time, 100 tool calls, 500 turns, 300 s per command. Container setup isn't counted in the agent's time, but the competition's 12 h cap includes it. | 7.1 |
| How does `eval_config.yaml` work? | An optional file at the submission root. Keys go under `evaluation:`: `max_time_minutes`, `max_tool_calls`, `max_turns`, and `timeout_seconds` (per command). The sample uses 1 min, 10 calls, 50 turns and 300 s. | 2.2, 7.1 |
| Is there a local evaluation tool? | Yes, `swegemma eval`. See [Local evaluation](#local-evaluation-9). | 9.1 |

## Submission schema (§2, §3)

- **Root config.** Exactly one of `agent.yaml`, `agent.yml`, `root_agent.yaml` or `root_agent.yml` must sit at the archive root. None raises `MissingRootConfigError`; two or more raise `MultipleRootConfigsError`. (§2.2)
- **Compiler.** `adk-submission`'s `compile_submission` builds the agent tree from YAML against closed registries, and never imports competitor Python. The schema is its `SandboxedAgentConfig`, not ADK's `LlmAgentConfig`. (§2.1, §2.3)
- **`agent_class`.** One of `LlmAgent` (the default), `SequentialAgent`, `ParallelAgent` or `LoopAgent` (`max_iterations` 1–500). (§2.3)
- **`LlmAgent` fields:**
  - `name` (required) and `model` (required unless a default is set)
  - `adapter`, `description`, and `instruction` (up to 1,000,000 chars)
  - `tools`, `skills` and `sub_agents`
  - `output_key` and `include_contents` (`default` or `none`)
  - `disallow_transfer_to_parent`, `disallow_transfer_to_peers` and `generate_content_config`
  
  (§2.3)
- **`tools`.** Each entry is a tool-name string (`- run_command`), an AgentTool reference (`- agent_tool: {config_path: ..., skip_summarization: true}`), or an inline agent dictionary. The README doesn't say whether `{name: ...}` mappings are also accepted. (§2.3, §3.2)
- **`skills`.** A list of relative paths to skill folders, each holding a `SKILL.md`, for example `skills/repo_navigation`. (§2.2, §2.3)
- **`sub_agents`.** A list of `{config_path: ...}` entries, used for multi-agent transfer. (§2.3)
- **`adapter: <name>`.** Points at `adapters/<name>/`, which holds `adapter_config.json` and `adapter_model.safetensors`. Names match `[A-Za-z0-9][A-Za-z0-9._-]*`, and the LoRA rank is at most 128. Each agent can use a different adapter. (§3.4)
- **`!include`.** `.md` and `.txt` files load as strings. `.yaml` and `.yml` files are parsed recursively, up to depth 10 with cycle detection. Absolute paths, null bytes, `..`, and symlinks that resolve outside the root are blocked (`PathTraversalError`). (§2.2) **Paths resolve from the including file's folder.** The official `data/sample_submission/sub_agents/code_analyzer.yaml` includes `../prompts/analyzer.md`, so `..` that stays inside the archive works. The competition overview still says `../` isn't allowed, so we keep every agent YAML at the archive root and never need it.
- **`generate_content_config`.** Allowed fields:
  - `temperature`, `top_p`, `top_k`, `seed` and `stop_sequences`
  - `presence_penalty`, `frequency_penalty` and `response_mime_type`
  - `max_output_tokens`: 1–32,768, default 16,384
  - `thinking_config`: `thinking_budget` (0–32,768, default 4,096), `thinking_level` and `include_thoughts`
  
  `tools`, `system_instruction`, `http_options`, `safety_settings` and `response_schema` are forbidden. With vLLM, set `include_thoughts: true` and a `thinking_budget` rather than `thinking_level`. (§2.4)
- **Limits.** Under 3 GiB unpacked including adapters. At most 10,000 files, 1,000 YAML files, 50 MiB per YAML file (including include expansion), 50 MiB per skill, 500 agents, nesting depth 50 and 1,000 skills. Only `.yaml .yml .md .txt .py .json .safetensors` files are allowed. (§2.4)
- **One model.** Every agent must declare the same base model, after provider prefixes are stripped. It must be one of the four ids in `ALLOWED_MODEL_NAMES`: `gemma-4-{31b,12b,e4b,e2b}-it-qat-w4a16-ct`. Anything else raises `ParticipantVisibleError`. The harness adds 12B's `--hf-overrides` flag itself. (§3.1, §3.2)

## Session protocol (§5, §7.2)

- **Session state.** `problem_description` is always set. `hints` is set only when the task's hints text isn't empty. Both can be used in `instruction` as `{problem_description}` and `{hints}`. (§5.1) LIKELY, from ADK's behaviour rather than the README: ADK fails on a missing variable, so write `{hints?}` or leave hints out of the instruction.
- **First user message.** It has up to seven sections, and only declared tools are mentioned in it. (§5.2) The system prompt doesn't need to repeat any of them.
  1. The problem statement
  2. Hints, if there are any
  3. The task budget
  4. Environment rules: 300 s per command, 5,000-character output, 150 lines and 10,000 chars per `read_file`, offline
  5. Standard instructions: work under `/workspace`, verify with targeted tests, call `submit_patch`, then end with a final text response
  6. Code-intelligence tool help, when graph data exists
  7. The workspace layout: up to 200 entries, depth 3
- **Loop.** It stops after the turn in which `submit_patch` is called, on timeout, when a budget runs out, or after more than 3 nudges in a row without a tool call. Tool errors go back to the model as JSON instead of ending the task. (§5.3)
- **Context.** The harness compacts and caches the context itself: compaction every 15 events, at a 14,336-token threshold, keeping 5 events. Transient model errors are retried up to 5 times. (§7.2)

## Tool behaviour (§6, §8.1, §10)

| Tool | Counts as a call | Behaviour |
| --- | --- | --- |
| `run_command(command)` | yes | Runs `bash -c` in `/workspace`. Times out at min(300 s, time left); a timeout doesn't end the session. stdout and stderr are each capped at 5,000 chars. |
| `read_file(filepath, start_line, end_line)` | yes | Returns at most 150 lines and 10,000 chars, plus `total_lines` and `is_truncated`. |
| `edit_file(filepath, old_string, new_string, allow_multiple)` | yes | Tries an exact match, then a whitespace-flexible match, then a regex match. Fails when more than one place matches (unless `allow_multiple`), and on empty files. |
| `write_file(filepath, content)` | yes | Creates parent folders as needed. |
| `get_code_neighbors(node, edge_type, max_neighbors)` | yes | Callers and callees of a **symbol name**. Resolves exact, then suffix, then case-insensitive, then substring. |
| `search_similar_code(query, k)` | yes | Takes a **symbol name** such as `pkg.mod.Class.method` or `parse_header`. Offline, so free text isn't embedded; multi-word queries fall back to token overlap. |
| `get_code_subgraph(nodes)` | yes | The induced subgraph for a list of symbols. |
| `get_status()` | no | Calls, time and turns left. |
| `submit_patch()` | no | Runs `git add -N .` and `git diff --binary`. The loop ends after the current turn. |

- **Budget warning.** With 20 or more calls budgeted and 10 or fewer left, every tool response carries a `budget_warning`. (§6)
- **Paths.** File paths resolve from `/workspace`. A leading `/workspace/` is stripped, and `..` is rejected. (§6.2)
- **Scratch files.** Any file the agent creates in `/workspace` ends up in the patch, so scratch scripts belong in `/tmp`. (§8.1, §10.3)
- **Protected files.** These are stripped from the patch:
  - test files: `test_*.py`, `*_test.py`, and `.py` files under `tests/`, `test/` or `testing/`
  - runner and config files: `conftest.py`, `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`, `.pytest.ini`, `sitecustomize.py`, `usercustomize.py`, `_swegemma_stubs.py` and `*.pth`
  
  Fixes have to live in library code. (§8.1)
- **No `submit_patch`.** The harness still extracts the diff and grades it. (§8.1)
- **Starter-kit advice:**
  - Keep edits small so a long thought doesn't cut off a tool call.
  - Leave `/workspace/pytest.ini` and `/workspace/conftest.py` alone.
  - Delegate exploration to an AgentTool with `skip_summarization: true`.
  - Call `submit_patch` last.
  
  (§10)

## Grading (§4, §8.2)

- **Two containers per task.** Container A runs the agent and Container B verifies. Each starts from a fresh snapshot at `base_commit` with no later git history, an editable install, cached test dependencies, and the harness's `pytest.ini` and `conftest.py` committed as the baseline. (§4, §4.2)
- **Sandbox limits.** No network, 4 GiB of RAM (exit 137 when it runs out), 2 vCPUs, `/workspace` as the working folder, and `TEST_TMPDIR=/tmp`. (§4.1)
- **Applying the patch.** The agent's patch gets four apply passes; if all four fail, the task is unresolved. Then the test files and protected files are reset, `test_patch` is applied, and pytest runs with JUnit XML and a fixed 300 s timeout. (§8.2)
- **Resolved** means pytest exits 0, the JUnit report shows more than 0 passed with 0 failures and 0 errors, and every required test (`FAIL_TO_PASS`, `PASS_TO_PASS`) passed without being skipped. The score is resolved tasks divided by total tasks. (§8.2)

## Local evaluation (§9)

```
swegemma eval --tasks tasks.jsonl --snapshots-dir snapshots --submission-dir <dir> \
  --results-dir results/<run> --sandbox docker --max-tool-calls 50 --max-time-minutes 30 --concurrency 2
```

- **Flags:**
  - `--task-id` / `--task-ids` to run specific tasks
  - `--sandbox {docker,subprocess}`
  - `--timeout-seconds` and `--verification-timeout-seconds`
  - `--concurrency N`, and `--shard-index k --num-shards M`
  - `--models-yaml` and `--skip-agent-patch`
- **Outputs** in the results folder: `summary.json`, `task_results.jsonl`, `patches/`, `test_outputs/`, `traces/` and `logs/`.
- **Mirror the scorer.** The CLI's default for tool calls is unlimited, while the scorer's is 100, so pass explicit budget flags.
- **Open:** where the `swegemma` package and `snapshots/` come from. See `docs/dataset_inventory.md`.

## What the pre-README notes got wrong

| Topic | Pre-README note | Official (§) |
| --- | --- | --- |
| `eval_config.yaml` keys | Top-level `per_task_timeout_seconds`, `max_turns_per_task`, `trigger_token_estimate` | Under `evaluation:`: `max_time_minutes`, `max_tool_calls`, `max_turns`, `timeout_seconds` (per command). The harness fixes compaction itself. (7.1, 7.2) |
| Tools entry format | ADK `ToolConfig` mappings (`- name: run_command`) | Tool-name strings (`- run_command`) (2.3) |
| AgentTool format | `- name: AgentTool` with `args.agent.config_path` | `- agent_tool: {config_path: ..., skip_summarization: true}` (2.3, 3.2) |
| Model allow-list | Conflicting; only 31b was certain | All four `*-it-qat-w4a16-ct` ids are in `ALLOWED_MODEL_NAMES` (3.1, 3.2) |
| Declaring skill tools | Unclear whether to declare `run_skill_script` / `load_skill_resource` | `tools:` takes the 9 registry tools (6). Skill tools (`load_skill`, `load_skill_resource`, `run_skill_script`) come from ADK's SkillToolset when `skills:` is set (5.3). Listing them under `tools:` isn't documented. |

## Getting the files

The Kaggle CLI doesn't read `.env` itself. From the repo root:

```
source .venv/Scripts/activate        # PowerShell: .venv\Scripts\Activate.ps1
python -m dotenv run -- kaggle competitions files gemma-4-developer-agent --page-size 200
python -m dotenv run -- kaggle competitions download gemma-4-developer-agent -f HARNESS_README.md -p data
```

## Sources

- `data/HARNESS_README.md`: the competition dataset file, 55,940 bytes, published 2026-10-13
- [Competition overview](https://www.kaggle.com/competitions/gemma-4-developer-agent/overview) and the [host post on new models](https://www.kaggle.com/competitions/gemma-4-developer-agent/discussion/748287)
- [gemma-agent-lab issue #24](https://github.com/jwj1342/gemma-agent-lab/issues/24): the model conflict, 12B serving and LoRA guidance
- Team repos: [gemmapilot](https://github.com/Zhiyilang/gemmapilot), [ennydev-2026](https://github.com/ennydev-2026/gemma-4-developer-agent), [rishaviitd](https://github.com/rishaviitd/kaggle.gemma.coding.agent), [M4-on-Github](https://github.com/M4-on-Github/Gemma4_Dev_Agent_Kaggle)
- [aiwiki entry](https://aiwiki.ai/wiki/gemma_4_developer_agent_competition)
