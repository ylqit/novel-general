#!/usr/bin/env python
"""Write or verify SHA-256 checksums for release archives."""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
CHECKSUM_FILE = DIST / "SHA256SUMS"


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml does not declare project.version.")
    return match.group(1)


def release_artifacts() -> tuple[Path, ...]:
    version = project_version()
    prefix = f"longform_novel_engine-{version}"
    wheels = sorted(DIST.glob(f"{prefix}-*.whl"))
    sdists = sorted(DIST.glob(f"{prefix}.tar.gz"))
    artifacts = [*wheels, *sdists]
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(
            f"Expected one wheel and one sdist for version {version} under dist/; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)."
        )
    return tuple(artifacts)


def checksum_payload() -> str:
    return "".join(
        f"{sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in release_artifacts()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Write dist/SHA256SUMS.")
    mode.add_argument("--check", action="store_true", help="Verify dist/SHA256SUMS.")
    args = parser.parse_args()

    expected = checksum_payload()
    if args.write:
        CHECKSUM_FILE.write_text(expected, encoding="utf-8", newline="\n")
        print(f"Wrote {CHECKSUM_FILE.relative_to(ROOT)}")
        return 0
    if not CHECKSUM_FILE.is_file() or CHECKSUM_FILE.read_text(encoding="utf-8") != expected:
        print("dist/SHA256SUMS is missing or stale")
        return 1
    print("Release checksums are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
