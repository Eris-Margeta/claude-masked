#!/usr/bin/env python3
"""Local Anthropic Messages proxy → api.x.ai (Grok).

Yume / Claude Code speak Anthropic HTTP. xAI's /v1/messages already
accepts that shape. This process only:
  - rewrites Claude model ids to grok-4.7 / grok-4.6
  - attaches a Grok OAuth access token from ~/.grok/auth.json
  - streams the upstream response through
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from models import map_model

HOST = os.environ.get("CLAUDE_MASKED_PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAUDE_MASKED_PROXY_PORT", "8317"))
UPSTREAM = os.environ.get("CLAUDE_MASKED_XAI_BASE", "https://api.x.ai").rstrip("/")
AUTH_PATH = os.path.expanduser(os.environ.get("CLAUDE_MASKED_GROK_AUTH", "~/.grok/auth.json"))
PID_FILE = os.path.expanduser("~/.claude-masked/proxy.pid")
LOG_FILE = os.path.expanduser("~/.claude-masked/proxy.log")


def _log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def _block_types(content) -> list:
    if isinstance(content, str):
        return ["text"]
    if not isinstance(content, list):
        return [type(content).__name__]
    return [str(b.get("type")) if isinstance(b, dict) else type(b).__name__ for b in content]


def sanitize_schema(node):
    """xAI rejects JSON Schema `required: null` and missing required on objects."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            if not isinstance(node.get("required"), list):
                node["required"] = []
        elif "required" in node and not isinstance(node.get("required"), list):
            node["required"] = []
        node.pop("$schema", None)
        for key, val in list(node.items()):
            node[key] = sanitize_schema(val)
        return node
    if isinstance(node, list):
        return [sanitize_schema(item) for item in node]
    return node


def sanitize_messages(data: dict) -> dict:
    """xAI /v1/messages only accepts user and assistant roles."""
    messages = data.get("messages")
    if not isinstance(messages, list):
        return data
    system_bits = []
    if isinstance(data.get("system"), str) and data["system"].strip():
        system_bits.append(data["system"])
    elif isinstance(data.get("system"), list):
        for block in data["system"]:
            if isinstance(block, dict) and block.get("type") == "text":
                system_bits.append(block.get("text") or "")
            elif isinstance(block, str):
                system_bits.append(block)
    cleaned = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = (msg.get("role") or "").lower()
        content = msg.get("content")
        if role in ("system", "developer"):
            if isinstance(content, str):
                system_bits.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        system_bits.append(str(block.get("text") or ""))
                    elif isinstance(block, str):
                        system_bits.append(block)
            continue
        if role == "tool":
            tool_id = msg.get("tool_use_id") or msg.get("tool_call_id") or ""
            text = content if isinstance(content, str) else json.dumps(content)
            cleaned.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": text,
                        }
                    ],
                }
            )
            continue
        if role not in ("user", "assistant"):
            # xAI 400: Invalid message role
            role = "user"
        if content is None or content == "" or content == []:
            continue
        out = {"role": role, "content": content}
        if role == "assistant" and msg.get("tool_calls"):
            out["content"] = content
        cleaned.append(out)
    merged = []
    for msg in cleaned:
        if merged and merged[-1]["role"] == msg["role"]:
            a, b = merged[-1]["content"], msg["content"]
            if isinstance(a, str) and isinstance(b, str):
                merged[-1]["content"] = a + "\n" + b
            elif isinstance(a, list) and isinstance(b, list):
                merged[-1]["content"] = a + b
            elif isinstance(a, list) and isinstance(b, str):
                merged[-1]["content"] = a + [{"type": "text", "text": b}]
            elif isinstance(a, str) and isinstance(b, list):
                merged[-1]["content"] = [{"type": "text", "text": a}] + b
            else:
                merged.append(msg)
        else:
            merged.append(msg)
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continue)"}]})
    data["messages"] = merged
    sys_text = "\n\n".join(s for s in system_bits if s and str(s).strip())
    if sys_text:
        data["system"] = sys_text
    return data


def rewrite_request(raw: bytes) -> tuple:
    if not raw:
        return raw, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if not isinstance(data, dict):
        return raw, None
    original = str(data.get("model") or "") or None
    if "model" in data:
        data["model"] = map_model(str(data.get("model") or ""))
    thinking = data.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        data["thinking"] = {"type": "adaptive"}
    before = [
        (m.get("role") if isinstance(m, dict) else type(m).__name__)
        for m in (data.get("messages") or [])
    ]
    data = sanitize_messages(data)
    data = sanitize_schema(data)
    after = [
        {"role": m.get("role"), "types": _block_types(m.get("content"))}
        for m in (data.get("messages") or [])
        if isinstance(m, dict)
    ]
    _log("roles in=" + json.dumps(before)[:500] + " out=" + json.dumps(after)[:1500])
    return json.dumps(data).encode(), original


def rewrite_body(raw: bytes) -> bytes:
    body, _ = rewrite_request(raw)
    return body


_GROK_MODEL_RE = re.compile(rb'"model"\s*:\s*"grok-[^"]*"')


def restamp_model(buf: bytes, original: Optional[str]) -> bytes:
    """Claude Code SDK rejects grok-* ids. Put the caller's Claude id back."""
    if not original or original.startswith("grok-"):
        return buf
    repl = b'"model":' + json.dumps(original).encode()
    return _GROK_MODEL_RE.sub(repl, buf)


def grok_access_token() -> str:
    env = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
    if env:
        return env
    with open(AUTH_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    rec = None
    for v in data.values() if isinstance(data, dict) else []:
        if isinstance(v, dict) and v.get("key"):
            rec = v
            break
    if not rec:
        raise RuntimeError("no grok auth in " + AUTH_PATH)
    exp = rec.get("expires_at")
    if exp:
        try:
            dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < datetime.now(timezone.utc) and rec.get("refresh_token"):
                refreshed = _refresh(rec)
                if refreshed:
                    return refreshed
        except ValueError:
            pass
    return rec["key"]


def _refresh(rec: dict) -> Optional[str]:
    issuer = (rec.get("oidc_issuer") or "https://auth.x.ai").rstrip("/")
    client_id = rec.get("oidc_client_id") or ""
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": rec["refresh_token"],
            "client_id": client_id,
        }
    ).encode()
    req = urllib.request.Request(
        issuer + "/oauth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        _log("refresh failed: " + type(exc).__name__)
        return None
    token = payload.get("access_token")
    if not token:
        return None
    rec["key"] = token
    if payload.get("refresh_token"):
        rec["refresh_token"] = payload["refresh_token"]
    if payload.get("expires_in"):
        rec["expires_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(AUTH_PATH, encoding="utf-8") as fh:
            whole = json.load(fh)
        for k, v in list(whole.items()):
            if isinstance(v, dict) and v.get("user_id") == rec.get("user_id"):
                whole[k] = rec
        with open(AUTH_PATH, "w", encoding="utf-8") as fh:
            json.dump(whole, fh)
    except OSError:
        pass
    return token


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        _log("%s - %s" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        if self.path in ("/", "/health"):
            body = b'{"ok":true,"via":"claude-masked"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        self._proxy()

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self) -> None:
        self._proxy()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        shown = None
        if self.command == "POST" and "messages" in self.path:
            raw, shown = rewrite_request(raw)
        token = grok_access_token()
        url = UPSTREAM + self.path
        headers = {
            "Content-Type": self.headers.get("Content-Type") or "application/json",
            "Authorization": "Bearer " + token,
            "x-api-key": token,
            "anthropic-version": self.headers.get("anthropic-version") or "2023-06-01",
        }
        beta = self.headers.get("anthropic-beta")
        if beta:
            headers["anthropic-beta"] = beta
        parsed = urllib.parse.urlparse(url)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        conn = conn_cls(parsed.hostname, port, timeout=600)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        try:
            conn.request(self.command, path, body=raw if raw else None, headers=headers)
            upstream = conn.getresponse()
        except Exception as exc:
            err = json.dumps({"type": "error", "error": {"message": str(exc)}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(err)
            return
        if upstream.status >= 400:
            err_body = upstream.read()
            _log("upstream %s %s %s" % (upstream.status, path, err_body[:2000]))
            dump = os.path.expanduser("~/.claude-masked/last-400.json")
            try:
                with open(dump, "wb") as fh:
                    fh.write(raw or b"")
            except OSError:
                pass
            self.send_response(upstream.status)
            self.send_header("Content-Type", upstream.getheader("Content-Type") or "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(err_body)
            conn.close()
            return
        self.send_response(upstream.status)
        ctype = upstream.getheader("Content-Type") or "application/json"
        self.send_header("Content-Type", ctype)
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            if "event-stream" in ctype:
                buf = b""
                while True:
                    chunk = upstream.read(1024)
                    if not chunk:
                        if buf:
                            self.wfile.write(restamp_model(buf, shown))
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self.wfile.write(restamp_model(line + b"\n", shown))
                        self.wfile.flush()
            else:
                body = upstream.read()
                self.wfile.write(restamp_model(body, shown))
        finally:
            conn.close()


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def serve() -> None:
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as fh:
        fh.write(str(os.getpid()))
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    _log("proxy listen %s:%s -> %s" % (HOST, PORT, UPSTREAM))
    httpd.serve_forever()


def ensure_running() -> str:
    """Start the proxy if needed. Return base URL."""
    if port_open(HOST, PORT):
        return "http://%s:%s" % (HOST, PORT)
    script = os.path.abspath(__file__)
    log_fh = open(LOG_FILE, "a", encoding="utf-8")
    proc = __import__("subprocess").Popen(
        [sys.executable, script, "serve"],
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    for _ in range(50):
        if port_open(HOST, PORT):
            return "http://%s:%s" % (HOST, PORT)
        threading.Event().wait(0.05)
    raise RuntimeError("claude-masked proxy failed to bind %s:%s (pid %s)" % (HOST, PORT, proc.pid))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve()
    else:
        print(ensure_running())
