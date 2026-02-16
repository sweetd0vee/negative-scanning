from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict


_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def load_dotenv(path: Path = Path(".env")) -> Dict[str, str]:
    """
    Minimal .env loader (no external deps).
    - Supports lines like: KEY=value, export KEY=value
    - Supports quoted values: "..." or '...'
    - Ignores blank lines and comments starting with #
    - Does NOT override already-set environment variables
    Returns dict of variables that were loaded.
    """
    loaded: Dict[str, str] = {}
    if not path.exists():
        return loaded

    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()

        # Strip inline comments for unquoted values: KEY=abc # comment
        if val and val[0] not in {"'", '"'}:
            if " #" in val:
                val = val.split(" #", 1)[0].rstrip()
            elif "\t#" in val:
                val = val.split("\t#", 1)[0].rstrip()

        # Unquote if quoted
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]

        if key not in os.environ:
            os.environ[key] = val
            loaded[key] = val

    return loaded

