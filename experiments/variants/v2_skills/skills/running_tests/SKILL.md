---
name: running_tests
description: How to reproduce an issue and run only the relevant tests, with short output, in a sandbox with no internet.
---

# Reproducing and testing

- **Find the nearest tests.** Look for `tests/test_<module>.py` or grep the tests folder for the function you are changing: `grep -rln "<function_name>" tests | head`.
- **Run one file or one test, not the suite:** `python -m pytest tests/test_x.py -x -q 2>&1 | tail -n 30`, or `python -m pytest "tests/test_x.py::test_name" -q 2>&1 | tail -n 30`.
- **Reproduce with a script** when no test covers the issue: write a few lines to `/tmp/repro.py` that trigger the bug, run `python /tmp/repro.py`, and confirm it fails the way the issue says. Keep the script out of the repository so it isn't part of the patch.
- **Rerun both after the fix:** the reproduction should now pass, and the nearby tests should still pass.
- **No internet:** `pip install` will fail. If a test needs a missing package, skip that test rather than trying to install it.
- **Don't edit existing tests** to make them pass. The grader applies its own tests to your patch.
