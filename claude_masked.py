#!/usr/bin/env python3
"""claude-masked: Claude Code / Yume CLI surface that runs Grok Build.

Yume spawns the official `claude` binary:
  --output-format stream-json --verbose --input-format stream-json -p
  --model claude-fable-5-1  (etc.)

This process accepts that argv, maps models/tools, runs `grok`, and
rewrites Grok streaming-json into Claude Code stream-json so Yume's UI works.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from typing import List, Optional, Sequence, Tuple
import re

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))

from models import LIST_MODELS, map_model, map_tools_csv
from stream_adapter import StreamTranslator, extract_user_text
from auth import handle_auth_argv

GROK_BIN_DEFAULT = os.path.expanduser("~/.local/bin/grok")
IDENTITY = "claude-masked"
SPOOF_VERSION = os.environ.get("CLAUDE_MASKED_SPOOF_VERSION", "2.1.266")
DEV25_AGENTS = os.environ.get(
    "CLAUDE_MASKED_AGENTS",
    os.path.expanduser("/Users/kovachevich/DEV-25/AGENTS.md"),
)

TAKES_VALUE = {
    "--cwd",
    "--debug-file",
    "--json-schema",
    "--max-turns",
    "--output-format",
    "--permission-mode",
    "--prompt-file",
    "--prompt-json",
    "--reasoning-effort",
    "--effort",
    "--resume",
    "--sandbox",
    "--session-id",
    "--tools",
    "--worktree",
    "--agent",
    "--agents",
    "--allow",
    "--deny",
    "--disallowed-tools",
    "--disallow-tools",
    "--allowed-tools",
    "--allowedTools",
    "--disallowedTools",
    "--model",
    "--append-system-prompt",
    "--system-prompt",
    "--system-prompt-file",
    "--fallback-model",
    "--max-budget-usd",
    "--mcp-config",
    "--settings",
    "--add-dir",
    "--input-format",
    "--memory-scope",
}

DROP_FLAGS = {
    "--add-dir",
    "--autocompact",
    "--ax-screen-reader",
    "--bare",
    "--betas",
    "--brief",
    "--chrome",
    "--disable-slash-commands",
    "--exclude-dynamic-system-prompt-sections",
    "--fallback-model",
    "--ide",
    "--include-hook-events",
    "--max-budget-usd",
    "--mcp-config",
    "--mcp-debug",
    "--memory-scope",
    "--no-session-persistence",
    "--plugin-dir",
    "--replay-user-messages",
    "--setting-sources",
    "--settings",
    "--strict-mcp-config",
    "--verbose",
}


def log(msg: str) -> None:
    if os.environ.get("CLAUDE_MASKED_DEBUG"):
        sys.stderr.write(f"[{IDENTITY}] {msg}\n")
        sys.stderr.flush()


def grok_bin() -> str:
    explicit = os.environ.get("CLAUDE_MASKED_GROK")
    if explicit:
        return explicit
    found = shutil.which("grok")
    if found:
        return found
    if os.path.isfile(GROK_BIN_DEFAULT) and os.access(GROK_BIN_DEFAULT, os.X_OK):
        return GROK_BIN_DEFAULT
    sys.stderr.write(
        f"{IDENTITY}: grok not found on PATH. Install Grok Build or set CLAUDE_MASKED_GROK.\n"
    )
    sys.exit(127)


def consume_value(argv: Sequence[str], i: int) -> Tuple[Optional[str], int]:
    cur = argv[i]
    if cur.startswith("--") and "=" in cur:
        return cur.split("=", 1)[1], i
    if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
        return argv[i + 1], i + 1
    return None, i


class Request:
    def __init__(self) -> None:
        self.print_mode = False
        self.output_format = "plain"  # caller-facing
        self.input_format: Optional[str] = None
        self.include_partial = False
        self.model_in: Optional[str] = None
        self.prompt: Optional[str] = None
        self.yolo = False
        self.permission_mode = "default"
        self.cwd = os.getcwd()
        self.session_id: Optional[str] = None
        self.resume: Optional[str] = None
        self.continue_ = False
        self.fork = False
        self.rules: List[str] = []
        self.grok_extra: List[str] = []
        self.want_stream_json = False


def parse(argv: Sequence[str]) -> Request:
    req = Request()
    prompt_parts: List[str] = []
    print_prompt: Optional[str] = None
    i = 0
    n = len(argv)

    while i < n:
        a = argv[i]

        if a in ("-h", "--help"):
            req.grok_extra.append("--help")
            i += 1
            continue
        if a in ("-v", "--version"):
            print(f"{SPOOF_VERSION} (Claude Code)")
            print(f"via {IDENTITY} → grok")
            sys.exit(0)
        if a == "--list-models":
            for m in LIST_MODELS:
                print(m)
            sys.exit(0)

        if a in ("-p", "--print"):
            req.print_mode = True
            if i + 1 < n and not argv[i + 1].startswith("-"):
                print_prompt = argv[i + 1]
                i += 2
                continue
            i += 1
            continue

        if a in ("-c", "--continue"):
            req.continue_ = True
            i += 1
            continue

        if a in ("-r", "--resume"):
            val, i = consume_value(argv, i)
            req.resume = val
            i += 1
            continue

        if a in ("-m", "--model") or a.startswith("--model="):
            val, i = consume_value(argv, i) if a in ("-m", "--model") else (a.split("=", 1)[1], i)
            req.model_in = val
            i += 1
            continue

        if a in (
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--yolo",
        ):
            req.yolo = True
            i += 1
            continue

        if a in ("--permission-mode",) or a.startswith("--permission-mode="):
            val, i = consume_value(argv, i) if a == "--permission-mode" else (a.split("=", 1)[1], i)
            req.permission_mode = val or "default"
            if req.permission_mode in ("bypassPermissions", "dontAsk"):
                req.yolo = True
            i += 1
            continue

        if a in ("--allowedTools", "--allowed-tools") or a.startswith("--allowed-tools=") or a.startswith("--allowedTools="):
            val, i = consume_value(argv, i) if a in ("--allowedTools", "--allowed-tools") else (a.split("=", 1)[1], i)
            if val:
                req.grok_extra.extend(["--tools", map_tools_csv(val)])
            i += 1
            continue

        if a in ("--disallowedTools", "--disallowed-tools", "--disallow-tools") or a.startswith(
            "--disallowed"
        ) or a.startswith("--disallow-tools="):
            val, i = consume_value(argv, i) if "=" not in a else (a.split("=", 1)[1], i)
            if val:
                req.grok_extra.extend(["--disallowed-tools", map_tools_csv(val)])
            i += 1
            continue

        if a in ("--append-system-prompt", "--system-prompt") or a.startswith("--append-system-prompt=") or a.startswith("--system-prompt="):
            val, i = consume_value(argv, i) if "=" not in a else (a.split("=", 1)[1], i)
            if val:
                req.rules.append(val)
            i += 1
            continue

        if a == "--system-prompt-file" or a.startswith("--system-prompt-file="):
            val, i = consume_value(argv, i) if a == "--system-prompt-file" else (a.split("=", 1)[1], i)
            if val and os.path.isfile(val):
                with open(val, encoding="utf-8") as fh:
                    req.rules.append(fh.read())
            i += 1
            continue

        if a == "--output-format" or a.startswith("--output-format="):
            val, i = consume_value(argv, i) if a == "--output-format" else (a.split("=", 1)[1], i)
            req.output_format = val or "plain"
            i += 1
            continue

        if a == "--input-format" or a.startswith("--input-format="):
            val, i = consume_value(argv, i) if a == "--input-format" else (a.split("=", 1)[1], i)
            req.input_format = val
            i += 1
            continue

        if a == "--include-partial-messages":
            req.include_partial = True
            i += 1
            continue

        if a == "--max-turns" or a.startswith("--max-turns="):
            val, i = consume_value(argv, i) if a == "--max-turns" else (a.split("=", 1)[1], i)
            if val:
                req.grok_extra.extend(["--max-turns", val])
            i += 1
            continue

        if a == "--cwd" or a.startswith("--cwd="):
            val, i = consume_value(argv, i) if a == "--cwd" else (a.split("=", 1)[1], i)
            if val:
                req.cwd = val
                req.grok_extra.extend(["--cwd", val])
            i += 1
            continue

        if a in ("-s", "--session-id") or a.startswith("--session-id="):
            val, i = consume_value(argv, i) if a in ("-s", "--session-id") else (a.split("=", 1)[1], i)
            req.session_id = val
            i += 1
            continue

        if a == "--fork-session":
            req.fork = True
            i += 1
            continue

        if a in ("--reasoning-effort", "--effort") or a.startswith("--reasoning-effort=") or a.startswith("--effort="):
            val, i = consume_value(argv, i) if a in ("--reasoning-effort", "--effort") else (a.split("=", 1)[1], i)
            if val:
                req.grok_extra.extend(["--reasoning-effort", val])
            i += 1
            continue

        base = a.split("=", 1)[0]
        if base in DROP_FLAGS or a in DROP_FLAGS:
            if "=" not in a and base in TAKES_VALUE and i + 1 < n and not argv[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            log(f"dropped Claude/Yume-only flag: {a}")
            continue

        if a.startswith("-"):
            req.grok_extra.append(a)
            if a in TAKES_VALUE and "=" not in a and i + 1 < n and not argv[i + 1].startswith("-"):
                req.grok_extra.append(argv[i + 1])
                i += 2
                continue
            i += 1
            continue

        prompt_parts.append(a)
        i += 1

    req.prompt = print_prompt if print_prompt is not None else (" ".join(prompt_parts) if prompt_parts else None)
    fmt = (req.output_format or "").lower()
    req.want_stream_json = fmt in ("stream-json", "stream_json", "streaming-json")
    return req


def load_dev25_agents() -> str:
    try:
        with open(DEV25_AGENTS, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def grok_env() -> dict:
    env = os.environ.copy()
    env["GROK_CLAUDE_AGENTS_ENABLED"] = "false"
    env["GROK_CLAUDE_HOOKS_ENABLED"] = "false"
    env["GROK_CLAUDE_MCPS_ENABLED"] = "false"
    env["GROK_CLAUDE_RULES_ENABLED"] = "false"
    env["GROK_CLAUDE_SKILLS_ENABLED"] = "false"
    return env


def grok_output_format(req: Request) -> Optional[str]:
    fmt = (req.output_format or "plain").lower()
    mapping = {
        "text": "plain",
        "plain": "plain",
        "json": "json",
        "stream-json": "streaming-json",
        "stream_json": "streaming-json",
        "streaming-json": "streaming-json",
        "stream-json-messages": "streaming-messages-json",
        "streaming-messages-json": "streaming-messages-json",
    }
    return mapping.get(fmt, fmt)


def build_grok_argv(req: Request, prompt: Optional[str], resume_id: Optional[str], new_session: bool) -> List[str]:
    args: List[str] = []
    args.extend(req.grok_extra)
    grok_fmt = grok_output_format(req)
    if grok_fmt and grok_fmt != "plain":
        # strip any previous --output-format from extra (shouldn't be there)
        args.extend(["--output-format", grok_fmt])
    args.extend(["--model", map_model(req.model_in)])
    if req.yolo:
        args.append("--yolo")
    if req.permission_mode and req.permission_mode != "default":
        args.extend(["--permission-mode", req.permission_mode])
    rules = list(req.rules)
    home = load_dev25_agents()
    if home and home not in rules:
        rules.insert(0, home)
    if rules:
        args.extend(["--rules", "\n\n".join(rules)])
    if resume_id:
        args.extend(["--resume", resume_id])
        if req.fork:
            args.append("--fork-session")
    elif new_session and req.session_id and _looks_uuid(req.session_id):
        args.extend(["--session-id", req.session_id])
    elif req.continue_:
        args.append("--continue")

    if req.print_mode or req.want_stream_json or req.input_format == "stream-json":
        args.extend(["-p", prompt or ""])
    elif prompt:
        args.append(prompt)
    return args


def run_grok_stream(req: Request, prompt: str, tr: StreamTranslator, resume_id: Optional[str], new_session: bool) -> int:
    argv = [grok_bin()] + build_grok_argv(req, prompt, resume_id, new_session)
    log("exec: " + " ".join(argv[:12]) + (" ..." if len(argv) > 12 else ""))
    env = grok_env()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        cwd=req.cwd if os.path.isdir(req.cwd) else None,
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        tr.feed_line(line, sys.stdout)
    return proc.wait()


def read_stdin_event() -> Optional[dict]:
    line = sys.stdin.readline()
    if line == "":
        return None
    line = line.strip()
    if not line:
        return {"type": "_blank"}
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": line}]}}


def stream_json_session(req: Request) -> int:
    """Yume protocol: NDJSON on stdin, Claude stream-json on stdout."""
    session_id = req.session_id or str(uuid.uuid4())
    shown = req.model_in or "claude-fable-5-1"
    tr = StreamTranslator(
        session_id=session_id,
        model_shown=shown,
        cwd=req.cwd,
        permission_mode=req.permission_mode,
        include_partial=req.include_partial or True,
    )
    grok_session: Optional[str] = req.resume
    first = True
    pending_prompt = req.prompt or ""

    # If Yume passed -p with no argv prompt, the first user event is on stdin.
    if pending_prompt:
        code = run_grok_stream(req, pending_prompt, tr, grok_session, new_session=first and not grok_session)
        grok_session = tr.grok_session_id or grok_session
        # Prefer grok's session from result if we stored it
        first = False
        if code != 0 and not req.input_format:
            return code

    if req.input_format != "stream-json":
        if not pending_prompt:
            # print mode with empty prompt: still one grok call (maybe stdin text)
            if not sys.stdin.isatty():
                text = sys.stdin.read()
                if text:
                    return run_grok_stream(req, text, tr, grok_session, new_session=True)
            return run_grok_stream(req, pending_prompt or "", tr, grok_session, new_session=True)
        return 0

    # Keep the process alive for Yume's stdin event loop.
    while True:
        ev = read_stdin_event()
        if ev is None:
            break
        et = ev.get("type")
        if et == "_blank":
            continue
        if et == "control_request":
            rid = ev.get("request_id") or (ev.get("request") or {}).get("request_id")
            sys.stdout.write(
                json.dumps(
                    {
                        "type": "control_response",
                        "response": {"subtype": "success", "request_id": rid},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        if et in ("user", "prompt"):
            text = extract_user_text(ev)
            if not text.strip():
                continue
            code = run_grok_stream(req, text, tr, grok_session, new_session=first and not grok_session)
            first = False
            grok_session = tr.grok_session_id or grok_session
            if code != 0:
                log(f"grok exit {code}")
            continue
        log(f"ignored stdin event type={et}")
    return 0


def exec_or_stream(req: Request) -> None:
    if req.want_stream_json or req.input_format == "stream-json":
        # Always translate; Yume cannot parse Grok ACP events.
        code = stream_json_session(req)
        sys.exit(code)

    argv = [grok_bin()] + build_grok_argv(req, req.prompt, req.resume, new_session=not req.resume)
    log("exec: " + " ".join(argv))
    os.execve(argv[0], argv, grok_env())


def _log_invocation(argv: Sequence[str]) -> None:
    try:
        log_dir = os.path.expanduser("~/.claude-masked")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "invocations.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": list(argv), "exe": sys.argv[0]}) + "\n")
    except OSError:
        pass


def main(argv: Optional[Sequence[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    _log_invocation(argv)
    if handle_auth_argv(argv):
        return
    req = parse(argv)
    exec_or_stream(req)


if __name__ == "__main__":
    main()
