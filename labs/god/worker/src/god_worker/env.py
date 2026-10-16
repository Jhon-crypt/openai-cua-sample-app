"""Load repo-root .env into process environment if present."""

from __future__ import annotations

import os
from pathlib import Path


def load_repo_env() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ".env"
        if candidate.is_file() and (parent / "labs" / "god").is_dir():
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1) 
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
            return candidate
    return None
