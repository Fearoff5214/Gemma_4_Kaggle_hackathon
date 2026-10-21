---
name: repo_navigation
description: Find the code an issue is about in few tool calls, using symbol names with the code-graph tools and grep for literal strings.
---

# Finding the code an issue is about

1. List what the issue names: modules, classes, functions and methods, plus any exact error message, string literal, CLI flag or config key.
2. Symbols go to the code-graph tools, which take symbol names, not sentences.
   - search_similar_code with a dotted path (pkg.module.Class.method) or a short name (Class.method, parse_header) finds the symbol and similar code, with snippets.
   - get_code_neighbors on that symbol shows its callers and callees. Pass edge_type, for example calls, to narrow the list.
   - get_code_subgraph with two or three symbols shows how they connect. It helps when the symptom and the bug are a few calls apart.
3. Literal strings go to grep through run_command: grep -rn "exact text" --include=*.py . | head -n 20. Add --exclude-dir=tests --exclude-dir=docs unless the issue is about tests or docs.
4. Read only what you need: read_file with start_line and end_line around the function, not the whole file.
5. Before editing, name the one function you believe is wrong and say why in one sentence. If you can't, run one more targeted search rather than reading whole files.

Don't list directory trees, because the task message already shows the workspace layout. Don't read files from the top.
