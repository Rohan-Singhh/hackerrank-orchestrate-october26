"""One command to produce every deliverable from a clean checkout.

    python code/scripts/finalize.py            # online if a key is exported, cache-first otherwise

Runs: tests -> samples (scored) -> full audit (output.csv) -> submission validator
-> usage report -> evaluation report -> code.zip
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
REPO = CODE.parent
PY = sys.executable


def run(*args, check=True):
    print("$", " ".join(str(a) for a in args))
    r = subprocess.run([PY, *args], cwd=str(REPO))
    if check and r.returncode != 0:
        sys.exit(r.returncode)


def main() -> int:
    run("-m", "pytest", str(CODE / "tests"), "-q")
    run(str(CODE / "main.py"), "--sample", "--no-trace")
    run(str(CODE / "main.py"), "--attribution-eval")
    run(str(CODE / "scripts" / "check_submission.py"))
    run(str(CODE / "evaluation" / "usage.py"), "--force")
    run(str(CODE / "evaluation" / "report.py"))
    run(str(CODE / "scripts" / "make_zip.py"))
    print("done: output.csv, code.zip, code/evaluation/*.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
