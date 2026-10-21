# Dataset inventory

The competition download is 524 files and 22.4 GB, and 21.5 GB of that is per-task repo snapshots. To work on prompts you need only `tasks.jsonl` (2 MB) and `HARNESS_README.md`. Everything else is per-task material for running the agent, which belongs in a Kaggle notebook attached to the competition, not on a laptop.

As of 2026-10-21. The listing comes from `kaggle competitions files gemma-4-developer-agent` (3 pages) and is saved in `data/_file_list_all.csv`, which is git-ignored.

## File groups

| Group | Files | Size | What it is |
| --- | --- | --- | --- |
| `HARNESS_README.md` | 1 | 56 KB | Official harness documentation; see `docs/harness_notes.md` |
| `tasks.jsonl` | 1 | 2.0 MB | The 129 public dev tasks (see below) |
| `snapshots/` | 129 | 21.5 GB | One `<repo>_<number>.tgz` per task: the repository at `base_commit`, including `.git`. fastapi snapshots are 211–335 MB, rich about 91–97 MB, requests about 36–38 MB, httpx 1.6 MB. |
| `graphs/` | 127 | 422 MB | One `<repo>_<base_commit>.json` per commit: a call graph (format below), 0.6–5.2 MB each |
| `embeddings/` | 127 | 468 MB | One `<repo>_<base_commit>.npz` per commit: one vector per code symbol, 0.9–5.9 MB each |
| `wheels/` | 124 | 28 MB | Offline Python wheels for the task sandboxes: cp312/cp313 manylinux builds plus pure-Python ones, with several versions of fastapi's dependencies (for example 50 starlette versions, pydantic 1 and 2, pytest 6, 8 and 9) |
| `sample_submission/` | 10 | 0.4 MB | Official example submission: `agent.yaml`, `eval_config.yaml`, `configs/sampling.yaml`, `prompts/system.md`, `prompts/analyzer.md`, `sub_agents/code_analyzer.yaml`, and two placeholder LoRA adapters (`adapters/main_lora`, `adapters/tool_lora`, 218 KB each) |
| `docker/` | 4 | 26 KB | `Dockerfile.public`, `Dockerfile.sandbox`, `imp.py`, `telnetlib.py` |
| `sandbox/` | 1 | 14 KB | `setup.py` |

`docker/`, `sandbox/` and `sample_submission/` are covered in `docs/sandbox_environment.md`.

## Public dev tasks (`tasks.jsonl`)

| Repo | Tasks |
| --- | --- |
| fastapi/fastapi | 67 |
| Textualize/rich | 48 |
| psf/requests | 13 |
| encode/httpx | 1 |
| **Total** | **129** |

- **Fields:** `instance_id` (for example `fastapi_15661`, which is also the snapshot's name), `repo`, `base_commit`, `patch` (the gold fix), `test_patch`, `problem_statement`, `hints_text` (empty in every task) and `created_at` (2023-07-29 to 2026-06-20).
- **No test list or test command.** There are no `FAIL_TO_PASS` or `PASS_TO_PASS` fields, so the harness must decide which tests to run.
- **Base commits:** 127 distinct commits. Two pairs of tasks share a commit (rich `4d6d631a…` and requests `7a13c041…`), so 127 graphs and 127 embeddings cover 129 tasks. Every commit has both a graph and an embedding.
- **Gold patches are small:** median 1 file and 12 changed lines, with the 90th percentile at 128 lines. 91 of the 129 touch a single file, and 3 change only docs. The median changed lines per repo are fastapi 18, rich 9, requests 4 and httpx 79.
- **Test patches touch only `tests/`:** median 1 file, maximum 29.
- **Problem statements:** median 418 characters, 90th percentile 1,891, maximum 10,095.

One example (`rich_4077`, shortened):

```
problem_statement: "proxy isatty\n\nFixes <url>"
patch:      rich/file_proxy.py   adds FileProxy.isatty() -> bool
test_patch: tests/test_file_proxy.py
```

## Graph and embedding format (checked on the httpx files)

- **Graph:** networkx node-link JSON with the keys `directed`, `multigraph`, `graph.repo_name`, `nodes` and `edges`. Each node is `{id, name, text}`, where `id` is the dotted symbol path and `text` is presumably its source code. Each edge is `{source, target, type, key}`, and every httpx edge has type `calls`. The httpx graph has 740 nodes and 1,618 edges.
- **Embeddings:** an `.npz` archive with one member per graph node (for example `httpx._content.Content`), each a 256-dimensional float32 vector. The httpx file has 740 vectors, matching the graph.
- These are presumably what `get_code_neighbors`, `get_code_subgraph` and `search_similar_code` query. The tools see only `calls` edges, so they show no imports or inheritance.

## What the team needs

| Need | Files | Where |
| --- | --- | --- |
| Reading issues, writing prompts, picking examples | `tasks.jsonl` | Anywhere; 2 MB |
| Running the agent on dev tasks | snapshots, graphs, embeddings, wheels | A notebook attached to the competition. Attached data is normally mounted read-only under `/kaggle/input/`, so nothing needs downloading; check the exact path in `HARNESS_README.md`. |
| LoRA data (Phase 2) | `tasks.jsonl` (a gold patch and test patch per task) plus snapshots | Kaggle notebook |
| Not worth downloading to a laptop | `snapshots/` (21.5 GB) | none |

## Open questions

- [ ] **Where do the harness and validator come from?** None of the 524 files is a harness package, validator, compile script or notebook. `docs/harness_notes.md` reported that the dataset ships `swegemma`/`adk-eval-core` and a validator. They may come from the notebook image, another Kaggle dataset or model, or pip; check `HARNESS_README.md`.
- [ ] **Which test command does the harness run** for each task, given that `tasks.jsonl` names no tests?
- [ ] **Why are fastapi snapshots 200–335 MB** when httpx is 1.6 MB? Likely git history or docs assets; it doesn't matter unless setup time per task depends on it.
- [ ] **Are the hidden tasks' graphs and embeddings built the same way,** so the tools behave the same as on the dev set?
- [ ] **May the gold patches be used as LoRA training data?** Check the rules before Phase 2.
