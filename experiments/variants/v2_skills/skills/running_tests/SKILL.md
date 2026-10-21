---
name: running_tests
description: Reproduce an issue with a scratch script in /tmp and check a fix with one targeted pytest run, without adding files to the patch.
---

# Reproducing and checking a fix

- **Scratch files go in /tmp, created with run_command.** For example run cat > /tmp/repro.py << 'EOF', then the script lines, then EOF, and run python3 /tmp/repro.py. Don't use write_file for scratch files: it writes under /workspace, and every new file there ends up in the patch.
- **Quick checks fit on one line:** python3 -c "import pkg; print(pkg.func(1))"
- **Find the nearest tests** for the code you're changing: grep -rln "function_name" tests | head -n 5
- **Run one file or one test, never the whole suite:** python3 -m pytest tests/test_x.py -q -x 2>&1 | tail -n 20, or add -k test_name to pick a single test. Your whole task budget is only a few minutes.
- **After the fix:** the reproduction should now pass, and the nearby tests should still pass.
- **Leave test files alone.** Edits to tests, conftest.py, pytest.ini, pyproject.toml, setup.cfg and tox.ini are stripped from the patch before grading, so fix the library code instead.
- **Clean up before you submit:** delete any scratch file you created inside /workspace, then call submit_patch last.
