"""Map Claude/Yume session UUIDs to Grok session IDs."""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

STORE = os.path.expanduser("~/.claude-masked/sessions.json")


def _load() -> Dict[str, str]:
    try:
        with open(STORE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, STORE)


def lookup(claude_sid: str) -> Optional[str]:
    if not claude_sid:
        return None
    data = _load()
    return data.get(claude_sid) or data.get("grok:" + claude_sid)


def remember(claude_sid: str, grok_sid: str) -> None:
    if not claude_sid or not grok_sid:
        return
    data = _load()
    data[claude_sid] = grok_sid
    data["grok:" + grok_sid] = grok_sid
    _save(data)
