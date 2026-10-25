"""Build code.zip for the HackerRank upload (README.md + code/, no dataset, no caches, no secrets)."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".pytest_cache", "audit_trace"}
EXCLUDE_SUFFIX = {".pyc", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".tmp"}
EXCLUDE_NAMES = {".env"}


def main() -> int:
    out = REPO / "code.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(REPO / "README.md", "README.md")
        for p in sorted((REPO / "code").rglob("*")):
            if p.is_dir():
                continue
            if any(part in EXCLUDE_DIRS for part in p.relative_to(REPO).parts):
                continue
            if p.suffix.lower() in EXCLUDE_SUFFIX or p.name in EXCLUDE_NAMES:
                continue
            zf.write(p, str(p.relative_to(REPO)).replace("\\", "/"))
            n += 1
    print(f"wrote {out} with {n + 1} files")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        bad = [x for x in names if x.endswith(tuple(EXCLUDE_SUFFIX)) or x.endswith(".env") or "dataset/" in x]
        if bad:
            print("problem entries:", bad)
            return 1
        print("contains:", ", ".join(sorted({x.split('/')[1] if x.startswith('code/') and '/' in x[5:] else x for x in names})))
    return 0


if __name__ == "__main__":
    sys.exit(main())
