"""Spoof Claude Code `auth` so Yume does not open Anthropic login."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List

GROK_AUTH = os.path.expanduser("~/.grok/auth.json")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def _parse_expiry(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    text = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def grok_session() -> Optional[Dict[str, Any]]:
    if os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY"):
        return {"email": "api-key", "auth_mode": "api_key", "expires_at": None}
    path = os.environ.get("CLAUDE_MASKED_GROK_AUTH", GROK_AUTH)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    now = datetime.now(timezone.utc)
    best = None
    for rec in data.values():
        if not isinstance(rec, dict):
            continue
        if not rec.get("key") and not rec.get("refresh_token") and not rec.get("access_token"):
            continue
        exp = _parse_expiry(rec.get("expires_at"))
        # Refresh tokens keep the session alive after access expiry.
        if exp is not None and exp < now and not rec.get("refresh_token"):
            continue
        best = rec
        break
    return best


def is_logged_in() -> bool:
    return grok_session() is not None


def status_payload() -> Dict[str, Any]:
    sess = grok_session()
    logged = sess is not None
    email = (sess or {}).get("email") or ""
    method = "none"
    if logged:
        mode = (sess or {}).get("auth_mode") or "oauth"
        method = "api_key" if mode == "api_key" else "oauth"
    return {
        "loggedIn": logged,
        "oauthLoggedIn": logged and method == "oauth",
        "hasApiKey": logged and method == "api_key",
        "authMethod": method,
        "apiProvider": "firstParty",
        "effectiveMode": method if logged else "none",
        "isCloudProvider": False,
        "analyticsDisabled": True,
        "projectsDirectory": PROJECTS_DIR,
        "account": {
            "email": email,
            "subscriptionType": "max" if logged else None,
        },
    }


def print_status(text: bool = False) -> None:
    payload = status_payload()
    if text:
        if payload["loggedIn"]:
            email = payload["account"].get("email") or "grok"
            print(f"Logged in as {email} (claude-masked → grok)")
        else:
            print("Not logged in. Run: claude auth login   # opens grok login")
        return
    print(json.dumps(payload))


def _grok_bin() -> str:
    explicit = os.environ.get("CLAUDE_MASKED_GROK")
    if explicit:
        return explicit
    found = shutil.which("grok")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/grok")
    if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
        return fallback
    sys.stderr.write("claude-masked: grok not found; cannot login.\n")
    sys.exit(127)


def exec_grok_login() -> None:
    bin_path = _grok_bin()
    sys.stderr.write("claude-masked: opening Grok login (auth.x.ai), not Anthropic.\n")
    os.execv(bin_path, [bin_path, "login"])


def exec_grok_logout() -> None:
    bin_path = _grok_bin()
    os.execv(bin_path, [bin_path, "logout"])


def handle_auth_argv(argv: list) -> bool:
    """If argv is a Claude auth/login command, handle it and return True."""
    if not argv:
        return False
    tokens = [a for a in argv if a != "--" and not a.startswith("-")]

    if "auth" in tokens:
        after = tokens[tokens.index("auth") + 1 :]
        sub = after[0] if after else "status"
        if sub in ("-h", "--help"):
            print("Usage: claude auth [login|logout|status]")
            sys.exit(0)
        if sub == "login":
            exec_grok_login()
            return True
        if sub == "logout":
            exec_grok_logout()
            return True
        if sub == "status":
            print_status(text="--text" in argv)
            sys.exit(0)
        print(f"claude-masked: unknown auth command {sub!r}", file=sys.stderr)
        sys.exit(2)

    if tokens and tokens[0] in ("/login", "login"):
        exec_grok_login()
        return True
    if tokens and tokens[0] in ("/logout", "logout"):
        exec_grok_logout()
        return True

    # Yume polls these on a timer. They must not spawn Grok.
    if tokens and tokens[0] in ("/usage", "usage"):
        print(
            "claude-masked → grok\n"
            "Current session\n"
            "5-hour limit: 1% used\n"
            "7-day (weekly) limit: 1% used\n"
        )
        sys.exit(0)
    if tokens and tokens[0] == "plugins":
        print("[]")
        sys.exit(0)
    if tokens and tokens[0] == "context":
        print("system: small\nconversation: grok session\ntools: grok built-ins\n")
        sys.exit(0)
    return False
