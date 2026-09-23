"""Grok `streaming-json` → Claude Code `stream-json` (what Yume parses)."""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, TextIO

from models import claude_tool_name


def _emit(obj: dict, out: TextIO) -> None:
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


def extract_user_text(event: dict) -> str:
    msg = event.get("message") or event.get("content") or event
    if isinstance(msg, str):
        return msg
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(event)
    bits: List[str] = []
    for block in content:
        if isinstance(block, str):
            bits.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            bits.append(block.get("text") or "")
        elif block.get("type") == "image":
            bits.append("[image]")
        elif "text" in block:
            bits.append(str(block["text"]))
    return "\n".join(b for b in bits if b)


class StreamTranslator:
    def __init__(
        self,
        session_id: str,
        model_shown: str,
        cwd: str,
        permission_mode: str = "default",
        include_partial: bool = True,
    ) -> None:
        self.session_id = session_id
        self.model_shown = model_shown
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.include_partial = include_partial
        self.started = time.time()
        self.text_buf = ""
        self.thought_buf = ""
        self.msg_n = 0
        self.tools: List[str] = []
        self.last_usage: Dict[str, Any] = {}
        self.last_cost: Optional[float] = None
        self.num_turns = 0
        self.init_emitted = False
        self.grok_session_id: Optional[str] = None

    def _msg_id(self) -> str:
        self.msg_n += 1
        return f"msg_{self.msg_n}"

    def emit_init(self, out: TextIO, tools: Optional[List[str]] = None) -> None:
        if self.init_emitted:
            return
        claude_tools = [claude_tool_name(t) for t in (tools or self.tools or [])]
        _emit(
            {
                "type": "system",
                "subtype": "init",
                "session_id": self.session_id,
                "cwd": self.cwd,
                "model": self.model_shown,
                "permissionMode": self.permission_mode,
                "apiKeySource": "login",
                "tools": claude_tools,
                "mcp_servers": [],
                "slash_commands": [],
                "output_style": "default",
            },
            out,
        )
        self.init_emitted = True

    def _assistant(self, content: list, out: TextIO, usage: Optional[dict] = None) -> None:
        message: Dict[str, Any] = {
            "id": self._msg_id(),
            "type": "message",
            "role": "assistant",
            "model": self.model_shown,
            "content": content,
        }
        if usage:
            message["usage"] = usage
        _emit({"type": "assistant", "session_id": self.session_id, "message": message}, out)

    def _flush_text(self, out: TextIO) -> None:
        if self.text_buf:
            self._assistant([{"type": "text", "text": self.text_buf}], out)
            self.text_buf = ""

    def feed_line(self, line: str, out: TextIO) -> None:
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        et = ev.get("type")

        if et == "available_commands":
            self.tools = list(ev.get("tools") or [])
            self.emit_init(out, self.tools)
            return

        if not self.init_emitted:
            self.emit_init(out)

        if et == "thought":
            chunk = ev.get("data") or ""
            self.thought_buf += chunk
            if self.include_partial and chunk:
                _emit(
                    {
                        "type": "stream_event",
                        "session_id": self.session_id,
                        "event": {
                            "type": "content_block_delta",
                            "delta": {"type": "thinking_delta", "thinking": chunk},
                        },
                    },
                    out,
                )
            return

        if et == "text":
            if self.thought_buf:
                self._assistant(
                    [{"type": "thinking", "thinking": self.thought_buf}],
                    out,
                )
                self.thought_buf = ""
            chunk = ev.get("data") or ""
            self.text_buf += chunk
            if self.include_partial and chunk:
                _emit(
                    {
                        "type": "stream_event",
                        "session_id": self.session_id,
                        "event": {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": chunk},
                        },
                    },
                    out,
                )
            return

        if et == "tool_call":
            self._flush_text(out)
            tid = ev.get("toolCallId") or f"toolu_{uuid.uuid4().hex[:8]}"
            name = claude_tool_name(ev.get("toolName") or ev.get("title") or "Unknown")
            raw = ev.get("rawInput") or {}
            self._assistant(
                [{"type": "tool_use", "id": tid, "name": name, "input": raw}],
                out,
            )
            return

        if et == "tool_call_update":
            tid = ev.get("toolCallId") or ""
            status = ev.get("status")
            if status in ("completed", "failed"):
                raw_out = ev.get("rawOutput")
                if raw_out is None:
                    content = json.dumps(ev.get("content") or "", ensure_ascii=False)
                elif isinstance(raw_out, str):
                    content = raw_out
                else:
                    content = json.dumps(raw_out, ensure_ascii=False)
                _emit(
                    {
                        "type": "user",
                        "session_id": self.session_id,
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tid,
                                    "content": content,
                                    "is_error": status == "failed",
                                }
                            ],
                        },
                    },
                    out,
                )
            return

        if et == "usage":
            self.last_usage = ev.get("usage") or {}
            return

        if et == "end":
            self._flush_text(out)
            if self.thought_buf:
                self._assistant(
                    [{"type": "thinking", "thinking": self.thought_buf}],
                    out,
                )
                self.thought_buf = ""
            usage = ev.get("usage") or self.last_usage
            self.last_usage = usage
            self.last_cost = ev.get("total_cost_usd")
            self.num_turns = ev.get("num_turns") or self.num_turns
            if ev.get("sessionId"):
                self.grok_session_id = ev["sessionId"]
            duration_ms = int((time.time() - self.started) * 1000)
            result_text = ""  # flushed already; Yume uses assistant events
            _emit(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": self.session_id,
                    "is_error": False,
                    "duration_ms": duration_ms,
                    "duration_api_ms": duration_ms,
                    "num_turns": self.num_turns or 1,
                    "result": result_text,
                    "usage": usage,
                    "modelUsage": ev.get("modelUsage") or {},
                    "total_cost_usd": self.last_cost,
                    "request_id": ev.get("requestId"),
                },
                out,
            )
            return

        if et == "error":
            _emit(
                {
                    "type": "result",
                    "subtype": "error",
                    "session_id": self.session_id,
                    "is_error": True,
                    "result": ev.get("message") or "error",
                    "error": ev.get("message") or "error",
                },
                out,
            )


def translate_grok_stream(
    lines: Iterator[str],
    out: TextIO,
    session_id: str,
    model_shown: str,
    cwd: str,
    permission_mode: str = "default",
    include_partial: bool = True,
) -> StreamTranslator:
    tr = StreamTranslator(session_id, model_shown, cwd, permission_mode, include_partial)
    for line in lines:
        tr.feed_line(line, out)
    return tr
