#!/usr/bin/env python3
"""Sync manifest.json requirements from pyproject.toml [project.dependencies]."""

import json
import re
import tomllib
from pathlib import Path


def main() -> None:
    """Sync manifest.json requirements from pyproject.toml."""
    root = Path(__file__).parent.parent

    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    deps = sorted(
        pyproject["project"]["dependencies"],
        key=lambda d: re.split(r"[><=!~\[]", d)[0].strip(),
    )

    manifest_path = root / "custom_components" / "proton_drive" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    if manifest.get("requirements") == deps:
        print("manifest.json requirements already up to date")
    else:
        manifest["requirements"] = deps
        manifest_path.write_text(json.dumps(manifest, indent="\t") + "\n")
        print(f"Updated manifest.json requirements: {deps}")


if __name__ == "__main__":
    main()
