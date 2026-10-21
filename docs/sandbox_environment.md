# Sandbox environment

What the agent's sandbox contains, and how the code-graph and embedding files are built. It's read from the competition files on 2026-10-21. The files were read as text only: nothing was built or run. **Confidence:** VERIFIED means read directly in the file named. LIKELY means inferred from it.

## The sandbox image

| Item | Value | Confidence | Source |
| --- | --- | --- | --- |
| Base image | `python:3.13-slim` (Debian) | VERIFIED | `data/docker/Dockerfile.sandbox` |
| Python | 3.13 | VERIFIED | same |
| OS packages added | `git`, `patch`, `protobuf-compiler`, `pigz` | VERIFIED | same |
| Python packages added | `pytest`, `pytest-timeout==2.1.0`, `typer`, and build backends (`setuptools`, `wheel`, `pdm-backend`, `poetry-core`, `hatchling`, `flit-core`, `editables`) | VERIFIED | same |
| Standard-library shims | `imp.py` (removed in 3.12; the file says it exists for flasgger) and `telnetlib.py` (removed in 3.13; a full copy of the old module), both copied into site-packages | VERIFIED | `data/docker/imp.py`, `data/docker/telnetlib.py` |
| Working directory | `/workspace` | VERIFIED | `Dockerfile.sandbox` |
| Test temp dir | `TEST_TMPDIR=/tmp` | VERIFIED | same |
| Git identity | `agent@eval` / `Agent`, default branch `main` | VERIFIED | same |
| User | No `USER` line, so root | LIKELY | same |
| Offline wheels | `/wheels`. The public image copies in the dataset's `wheels/` folder (124 wheels, 26.5 MB). The sandbox image only creates an empty folder there. | VERIFIED | `Dockerfile.public`, file listing |
| Not installed | `rg` (ripgrep), `curl`, `wget`, editors. `grep`, `sed`, `find`, `diff` and `git grep` come with the base image. | LIKELY | slim base plus the apt list above |
| Network | Not set in the Dockerfiles; check `HARNESS_README.md` | UNSURE | |

## How the sandbox sets up a repo

`data/samples/setup.py` (shipped as `sandbox/setup.py`) says it "runs inside the evaluation sandbox container prior to test execution". It doesn't say whether it also runs before the agent starts. It does the following:

- Adds `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.egg-info/`, `build/`, `dist/` and `.coverage` to `.git/info/exclude`, so that `git diff` stays clean.
- Installs the repo in editable mode with no dependencies: `pip install --no-index --find-links=/wheels --no-build-isolation --no-deps -e /workspace`. It then installs any dependencies that match wheels in `/wheels`.
- **Writes or edits `pytest.ini` and `conftest.py` at the repo root.** It sets `addopts = --import-mode=importlib -p no:anyio`, test discovery patterns, and filters that ignore `DeprecationWarning` and `UserWarning`. Neither file is on the git-exclude list.

## Code graph: `graphs/<repo>_<sha>.json`

The graph is in NetworkX node-link format: a directed multigraph with one file per repo snapshot. The sample is `data/samples/httpx_4acf5c2c37714cc63b5cf71b3e284fca83c90311.json`, with 740 nodes and 1,618 edges (VERIFIED).

| Part | Shape | Notes |
| --- | --- | --- |
| Top level | `directed: true`, `multigraph: true`, `graph: {repo_name}`, `nodes`, `edges` | `repo_name` is `<repo>_<sha>` |
| Node | `{id, name, text}` | `id` and `name` are the same dotted name, for example `httpx._content.Form.encode`. `text` is the source code; for classes, method bodies are cut to `...` (68 of 740 nodes). Text length runs from 25 to 5,692 characters, median 182. |
| Edge | `{type, source, target, key}` | Every edge has `type: "calls"`, but 482 of them link a class to its own methods, and others link a base class to a subclass. So "calls" covers containment, inheritance-like links and real calls. No edge points at a missing node. |
| Coverage | Covers both library and test code: 144 nodes are under `tests.*`. The sample repo also has an `ahttpx` async copy. | |

## Embeddings: `embeddings/<repo>_<sha>.npz`

Each node gets one `.npy` member, named by its node id: 740 vectors, and their keys match the graph's node ids exactly. Each vector is float32 with shape (256,). They aren't normalized: the first five have L2 norms of 5.7 to 5.9 (VERIFIED; headers and five vectors read without numpy or pickle).

## How the files likely back the tools

All of this is LIKELY, inferred from the file formats. Check the argument names in `HARNESS_README.md`.

- `search_similar_code`: embeds the query with the same 256-dim model and returns the node ids (and maybe their text) with the most similar vectors.
- `get_code_neighbors`: a node's incoming and outgoing edges, so its callers, callees, parent class and methods.
- `get_code_subgraph`: the nodes within a few hops of a node.
- The graph is precomputed for the original snapshot, **so it doesn't show the agent's edits.**

## Suggested prompt and skill changes

These are suggestions only. None of the prompt or skill files were changed.

1. **Tests: add `--timeout=60`.** `pytest-timeout` is installed, so `python -m pytest <file> -x -q --timeout=60 2>&1 | tail -n 30` stops one hanging test from eating the per-task budget.
2. **Missing packages: try the offline wheels before giving up.** `pip install --no-index --find-links /wheels <pkg>` may work in the public image. The private image's `/wheels` content is unknown.
3. **Search: prefer `git grep -n "<text>" -- '*.py'` over `grep -r`.** It skips ignored files and is faster. There's no `rg`.
4. **Graph tools: pass dotted ids exactly as returned** (`module.Class.method`). Use `read_file` to see code after an edit, because the graph shows the original snapshot.
5. **Results include tests.** `search_similar_code` can return test code. That helps for finding the test to run, but the fix belongs in library code.
6. **Leave `pytest.ini` and `conftest.py` alone** at the repo root. If setup runs before the agent, `git status` may show them changed by setup, not by the agent.
7. **Review the patch before submitting.** Run `git diff` before `submit_patch`, keep the diff small, and put scratch scripts in `/tmp`.
8. **Python 3.13.** Only `imp` and `telnetlib` are shimmed. An `ImportError` for another removed module, such as `cgi` or `pipes`, comes from the environment, not from the bug.
