"""Bootstrap dependencies without pip's temp directory.

Normally: `pip install -r requirements.txt`.
This fallback downloads PyPI wheels directly and unpacks them into the venv's
site-packages, because pip's temp-dir cleanup can be blocked in restricted
Windows environments.

Usage:
    python scripts/bootstrap_deps.py duckdb==2.0.0 sqlglot
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEEL_DIR = ROOT / ".wheels"
SITE_PACKAGES = ROOT / ".venv" / "Lib" / "site-packages"

_ABI3 = re.compile(r"-cp(\d)(\d+)-abi3-win_amd64\.whl$")
_CP = re.compile(r"-cp(\d)(\d+)-cp\1\2-win_amd64\.whl$")


def _accept(filename: str) -> int:
    """Return a preference score (lower is better); -1 means incompatible."""
    if not filename.endswith(".whl"):
        return -1
    cur = (sys.version_info.major, sys.version_info.minor)
    if filename.endswith("-py3-none-any.whl") or filename.endswith("-py2.py3-none-any.whl"):
        return 50
    m = _CP.search(filename)
    if m:
        return 0 if (int(m.group(1)), int(m.group(2))) == cur else -1
    m = _ABI3.search(filename)
    if m:
        # abi3 wheels are forward compatible: cp312-abi3 runs on cp314.
        return 10 if (int(m.group(1)), int(m.group(2))) <= cur else -1
    return -1


def pick_wheel(files: list[dict]) -> dict | None:
    scored: list[tuple[int, dict]] = []
    for f in files:
        score = _accept(f["filename"])
        if score >= 0:
            scored.append((score, f))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0])
    return scored[0][1]


def install(requirement: str) -> None:
    name, _, version = requirement.partition("==")
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=60) as resp:
        meta = json.load(resp)

    if not version:
        version = meta["info"]["version"]
    files = meta["releases"].get(version) or []

    wheel = pick_wheel(files)
    if wheel is None:
        raise SystemExit(f"{name}=={version}: no compatible wheel for this interpreter")

    WHEEL_DIR.mkdir(parents=True, exist_ok=True)
    target = WHEEL_DIR / wheel["filename"]
    if not target.exists():
        print(f"downloading {target.name} ({wheel['size'] / 1048576:.1f} MB)")
        with urllib.request.urlopen(wheel["url"], timeout=300) as resp, target.open("wb") as fh:
            while chunk := resp.read(1 << 20):
                fh.write(chunk)

    SITE_PACKAGES.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target) as zf:
        zf.extractall(SITE_PACKAGES)
    print(f"installed {name}=={version}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for req in sys.argv[1:]:
        install(req)
