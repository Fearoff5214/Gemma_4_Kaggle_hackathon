---
name: repo_navigation
description: How to find the code an issue is about, quickly, with the code-graph tools first and grep as a fallback.
---

# Finding the code an issue is about

1. Pull the search terms out of the issue: function, class and module names, error messages, CLI flags, config keys. Exact strings from tracebacks are the best leads.
2. Run `search_similar_code` with the issue's own wording or a quoted error message. Take the top few hits as candidates.
3. For each candidate, run `get_code_neighbors` to see its callers and callees. Use `get_code_subgraph` when the bug may sit a few calls away from where the symptom shows.
4. Read only what you need with `read_file`: the candidate function and its immediate neighbours, not whole files.
5. Fall back to `run_command` with `grep -rn "<exact string>" --include=*.py . | head -n 30` when the graph tools miss, for example for string literals, error messages or config keys.
6. Before editing, name the one function you believe is wrong and say why in a sentence. If you can't, keep locating.

Avoid listing whole directory trees or reading files top to bottom: both burn the turn budget.
