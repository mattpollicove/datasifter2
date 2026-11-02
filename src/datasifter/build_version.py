"""Reserve a persistent version for each standalone build."""

from __future__ import annotations

import re
from pathlib import Path


def reserve_build_version(version_file: Path, override: str | None = None) -> str:
    source = version_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "(\d+)\.(\d{3})"$', source, re.MULTILINE)
    if match is None:
        raise ValueError("Build version must use major.thousandths format, for example 0.001.")
    if override is None:
        value = int(match[1]) * 1000 + int(match[2]) + 1
        version = f"{value // 1000}.{value % 1000:03d}"
    else:
        if re.fullmatch(r"(?:0|[1-9]\d*)\.\d{3}", override) is None:
            raise ValueError("Explicit build version must use major.thousandths format.")
        version = override
    updated = source[:match.start()] + f'__version__ = "{version}"' + source[match.end():]
    temporary = version_file.with_suffix(".build-version.tmp")
    try:
        temporary.write_text(updated, encoding="utf-8")
        temporary.replace(version_file)
    finally:
        temporary.unlink(missing_ok=True)
    return version
