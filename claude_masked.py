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

from models import LIST_MODELS, map_disallowed_csv, map_model, map_tools_csv
from sessions import lookup as lookup_session, remember as remember_session
from stream_adapter import StreamTranslator, extract_user_text
from auth import handle_auth_argv
from api_proxy import ensure_running as ensure_api_proxy

GROK_BIN_DEFAULT = os.path.expanduser("~/.local/bin/grok")
IDENTITY = "claude-masked"
SPOOF_VERSION = os.environ.get("CLAUDE_MASKED_SPOOF_VERSION", "2.1.266")
_DEFAULT_AGENTS = os.path.expanduser("/Users/kovachevich/DEV-25/AGENTS.md")

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
        self.mcp_config: Optional[str] = None


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
                mapped = map_disallowed_csv(val)
                if mapped:
                    req.grok_extra.extend(["--disallowed-tools", mapped])
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

        if a == "--mcp-config" or a.startswith("--mcp-config="):
            val, i = consume_value(argv, i) if a == "--mcp-config" else (a.split("=", 1)[1], i)
            req.mcp_config = val
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
    path = os.environ.get("CLAUDE_MASKED_AGENTS") or _DEFAULT_AGENTS
    try:
        with open(path, encoding="utf-8") as fh:
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
    # Yume points ANTHROPIC_BASE_URL at its thinking-proxy → api.anthropic.com.
    # Grok must not inherit that.
    for key in list(env):
        if key.startswith("ANTHROPIC"):
            env.pop(key, None)
    return env


YUME_SHELL_REWRITE = (
    "IMPORTANT: this process is Grok Build (claude-masked). "
    "Use the built-in shell tool (run_terminal_command) for terminal work. "
    "mcp__yume__RunBash is not wired. Do not wait for MCP bash tools."
)


def rewrite_yume_rules(text: str) -> str:
    lowered = text.lower()
    if "mcp__yume__runbash" in lowered or "never use the built-in bash" in lowered:
        return YUME_SHELL_REWRITE + "\n\n" + text
    return text


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
    rules = [rewrite_yume_rules(r) for r in req.rules]
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


_PROBE_SLASH = ("/usage", "/context", "/cost", "/help", "/status")


def _is_probe_slash(prompt: str) -> bool:
    head = (prompt or "").strip().split()[0] if prompt else ""
    return head in _PROBE_SLASH


def _emit_slash_stream(tr: StreamTranslator, prompt: str) -> None:
    """Yume polls /context and /usage via `claude --resume ID -p /context`."""
    head = prompt.strip().split()[0]
    if head == "/context":
        body = (
            "Context (claude-masked → grok)\n"
            "system: small\n"
            "conversation: in-session (resumed)\n"
            "tools: grok built-ins\n"
        )
    elif head in ("/usage", "/cost"):
        body = "5-hour limit: 1% used\n7-day limit: 1% used\n"
    else:
        body = f"{head}: ok (claude-masked)"
    tr.emit_init(sys.stdout, tools=["Bash", "Read", "Write", "Edit"])
    tr.feed_line(json.dumps({"type": "text", "data": body}), sys.stdout)
    tr.feed_line(
        json.dumps(
            {
                "type": "end",
                "stopReason": "end_turn",
                "sessionId": tr.session_id,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "num_turns": 0,
                "total_cost_usd": 0,
            }
        ),
        sys.stdout,
    )


def plan_session(req: Request) -> tuple[str, Optional[str], bool]:
    """Return (emit_id, grok_resume_id, is_resume).

    Yume follows up with `claude --resume <uuid> -p …`. That UUID must be the
    same id Grok created (`grok --session-id`), or Grok starts a blank session
    and the UI looks like the conversation expired.
    """
    if req.resume:
        grok_id = lookup_session(req.resume) or req.resume
        return req.resume, grok_id, True
    emit = req.session_id if req.session_id and _looks_uuid(req.session_id) else str(uuid.uuid4())
    return emit, None, False


def stream_json_session(req: Request) -> int:
    """Yume protocol: NDJSON on stdin, Claude stream-json on stdout."""
    emit_id, grok_resume, is_resume = plan_session(req)
    req.session_id = emit_id
    if is_resume:
        req.resume = grok_resume
    shown = req.model_in or "claude-fable-5-1"
    tr = StreamTranslator(
        session_id=emit_id,
        model_shown=shown,
        cwd=req.cwd,
        permission_mode=req.permission_mode,
        include_partial=req.include_partial or True,
    )
    grok_session: Optional[str] = grok_resume
    first = not is_resume
    pending_prompt = req.prompt or ""

    if pending_prompt and _is_probe_slash(pending_prompt):
        _emit_slash_stream(tr, pending_prompt)
        return 0

    # If Yume passed -p with no argv prompt, the first user event is on stdin.
    if pending_prompt:
        code = run_grok_stream(req, pending_prompt, tr, grok_session, new_session=first and not grok_session)
        if tr.grok_session_id:
            remember_session(emit_id, tr.grok_session_id)
            grok_session = tr.grok_session_id
        else:
            grok_session = grok_session or emit_id
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
            if _is_probe_slash(text):
                _emit_slash_stream(tr, text)
                continue
            code = run_grok_stream(req, text, tr, grok_session, new_session=first and not grok_session)
            first = False
            if tr.grok_session_id:
                remember_session(emit_id, tr.grok_session_id)
                grok_session = tr.grok_session_id
            else:
                grok_session = grok_session or emit_id
            if code != 0:
                log(f"grok exit {code}")
            continue
        log(f"ignored stdin event type={et}")
    return 0


def real_claude_bin() -> str:
    for path in (
        os.environ.get("CLAUDE_MASKED_REAL_CLAUDE"),
        os.path.expanduser("~/.local/bin/claude-anthropic-homebrew"),
        os.path.expanduser("~/.local/bin/claude-anthropic"),
        os.path.expanduser("~/.local/share/claude/versions/2.1.266"),
    ):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            real = os.path.realpath(path)
            if "claude-masked" not in real:
                return path
    sys.stderr.write("claude-masked: real Claude Code binary not found (claude-anthropic).\n")
    sys.exit(127)


def is_api_mask(req: Request, argv: Sequence[str]) -> bool:
    mode = (os.environ.get("CLAUDE_MASKED_MODE") or "").strip().lower()
    if mode in ("harness", "grok"):
        return False
    if mode in ("api", "yume"):
        return True
    if os.environ.get("YUME_SESSION_ID") or os.environ.get("THINKING_PROXY_PORT"):
        return True
    if req.mcp_config:
        return True
    if req.want_stream_json and req.print_mode:
        return True
    if "--include-partial-messages" in argv or "--include-hook-events" in argv:
        return True
    return False


def claude_api_env() -> dict:
    base = ensure_api_proxy()
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = base
    env["ANTHROPIC_API_KEY"] = "sk-ant-api03-claude-masked"
    env["ANTHROPIC_AUTH_TOKEN"] = "sk-ant-api03-claude-masked"
    # Claude Code's SDK allowlists Claude ids. Grok ids go on the wire only
    # (rewritten in api_proxy). Fallback/haiku is Yume's second-turn model.
    env["ANTHROPIC_DEFAULT_FABLE_MODEL"] = "claude-fable-5-1"
    env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = "claude-opus-4-8"
    env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = "claude-sonnet-4-6"
    env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = "claude-haiku-4-5"
    env["ANTHROPIC_SMALL_FAST_MODEL"] = "claude-haiku-4-5"
    env.pop("ANTHROPIC_MODEL", None)
    env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") or "500000"
    env["DISABLE_AUTOUPDATER"] = "1"
    env.pop("ANTHROPIC_UPSTREAM_URL", None)
    return env


def exec_real_claude(argv: Sequence[str]) -> None:
    bin_path = real_claude_bin()
    env = claude_api_env()
    log("api-mask exec: " + bin_path + " " + " ".join(list(argv)[:8]))
    os.execve(bin_path, [bin_path] + list(argv), env)


def exec_or_stream(req: Request) -> None:
    argv = list(sys.argv[1:])
    if is_api_mask(req, argv):
        exec_real_claude(argv)
        return
    if req.want_stream_json or req.input_format == "stream-json":
        code = stream_json_session(req)
        sys.exit(code)

    grok_argv = [grok_bin()] + build_grok_argv(req, req.prompt, req.resume, new_session=not req.resume)
    log("exec: " + " ".join(grok_argv))
    os.execve(grok_argv[0], grok_argv, grok_env())


_REDACT_ENV = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _log_invocation(argv: Sequence[str]) -> None:
    try:
        log_dir = os.path.expanduser("~/.claude-masked")
        os.makedirs(log_dir, exist_ok=True)
        env = {}
        for k, v in os.environ.items():
            if k.startswith(("ANTHROPIC", "CLAUDE", "YUME", "GROK", "CLAUDE_MASKED")):
                if any(p in k.upper() for p in _REDACT_ENV):
                    env[k] = "<redacted>"
                else:
                    env[k] = v[:200]
        rec = {
            "argv": list(argv),
            "exe": sys.argv[0],
            "cwd": os.getcwd(),
            "ppid": os.getppid(),
            "stdin_tty": sys.stdin.isatty(),
            "env": env,
        }
        with open(os.path.join(log_dir, "invocations.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        with open(os.path.join(log_dir, "spy.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
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
