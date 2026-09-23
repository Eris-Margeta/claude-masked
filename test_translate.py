#!/usr/bin/env python3
"""Adapter tests. Run: python3 test_translate.py"""

import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import is_fable, map_disallowed_csv, map_model, map_tools_csv
from api_proxy import restamp_model, rewrite_body, sanitize_messages, sanitize_schema
from stream_adapter import StreamTranslator, extract_user_text
from auth import handle_auth_argv, status_payload
import claude_masked as cm


class ModelMapTests(unittest.TestCase):
    def test_fable_is_flagship(self):
        self.assertTrue(is_fable("fable"))
        self.assertTrue(is_fable("claude-fable-5-1"))
        self.assertTrue(is_fable("anthropic.claude-fable-5"))
        self.assertEqual(map_model("fable"), "grok-4.7")
        self.assertEqual(map_model("claude-fable-5-1"), "grok-4.7")
        self.assertEqual(map_model("best"), "grok-4.7")

    def test_lesser_is_standard(self):
        self.assertFalse(is_fable("sonnet"))
        self.assertEqual(map_model("sonnet"), "grok-4.6")
        self.assertEqual(map_model("opus"), "grok-4.6")
        self.assertEqual(map_model("haiku"), "grok-4.6")
        self.assertEqual(map_model("claude-opus-5"), "grok-4.6")
        self.assertEqual(map_model("claude-sonnet-4-6"), "grok-4.6")
        self.assertEqual(map_model(None), "grok-4.6")

    def test_tools(self):
        self.assertEqual(map_tools_csv("Bash Edit Read"), "run_terminal_command,search_replace,read_file")

    def test_disallowed_keeps_grok_shell(self):
        self.assertEqual(
            map_disallowed_csv("EnterPlanMode,ExitPlanMode,AskUserQuestion,Bash,PowerShell"),
            "",
        )

    def test_fable_bracket_1m(self):
        self.assertTrue(is_fable("claude-fable-5[1m]"))
        self.assertEqual(map_model("claude-fable-5[1m]"), "grok-4.7")


class ParseTests(unittest.TestCase):
    def test_print_prompt(self):
        req = cm.parse(["-p", "fix the tests"])
        self.assertTrue(req.print_mode)
        self.assertEqual(req.prompt, "fix the tests")

    def test_yume_stream_flags(self):
        req = cm.parse(
            [
                "--output-format",
                "stream-json",
                "--verbose",
                "--input-format",
                "stream-json",
                "-p",
                "--model",
                "claude-fable-5-1",
                "--include-partial-messages",
                "--dangerously-skip-permissions",
            ]
        )
        self.assertTrue(req.want_stream_json)
        self.assertEqual(req.input_format, "stream-json")
        self.assertEqual(req.model_in, "claude-fable-5-1")
        self.assertTrue(req.yolo)
        self.assertTrue(req.include_partial)

    def test_json_output_maps(self):
        req = cm.parse(["--print", "--output-format", "json", "summarize this"])
        self.assertEqual(req.output_format, "json")
        self.assertEqual(req.prompt, "summarize this")
        argv = cm.build_grok_argv(req, req.prompt, None, True)
        self.assertIn("json", argv)
        self.assertIn("grok-4.6", argv)

    def test_fable_in_grok_argv(self):
        req = cm.parse(["-p", "--model", "fable", "x"])
        argv = cm.build_grok_argv(req, "x", None, True)
        i = argv.index("--model")
        self.assertEqual(argv[i + 1], "grok-4.7")

    def test_injects_dev25_agents(self):
        fixture = os.path.join(os.path.dirname(__file__), "test_agents.md")
        with open(fixture, "w", encoding="utf-8") as fh:
            fh.write("Canonical workspace invariant\n/Users/kovachevich/DEV-25\n")
        self.addCleanup(lambda: os.path.exists(fixture) and os.remove(fixture))
        with mock.patch.dict(os.environ, {"CLAUDE_MASKED_AGENTS": fixture}):
            req = cm.parse(["-p", "x"])
            argv = cm.build_grok_argv(req, "x", None, True)
        self.assertIn("--rules", argv)
        rules = argv[argv.index("--rules") + 1]
        self.assertIn("Canonical workspace invariant", rules)
        self.assertIn("/Users/kovachevich/DEV-25", rules)

    def test_skips_rules_when_agents_missing(self):
        with mock.patch.dict(os.environ, {"CLAUDE_MASKED_AGENTS": "/tmp/claude-masked-no-such-agents.md"}):
            req = cm.parse(["-p", "x"])
            argv = cm.build_grok_argv(req, "x", None, True)
        self.assertNotIn("--rules", argv)

    def test_continue(self):
        req = cm.parse(["-c"])
        self.assertTrue(req.continue_)

    def test_version_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            with mock.patch("builtins.print"):
                cm.parse(["--version"])
        self.assertEqual(ctx.exception.code, 0)


class StreamTests(unittest.TestCase):
    def test_text_and_result(self):
        tr = StreamTranslator("sess", "claude-fable-5-1", "/tmp")
        buf = io.StringIO()
        tr.feed_line(
            json.dumps({"type": "available_commands", "tools": ["read_file", "write"]}),
            buf,
        )
        tr.feed_line(json.dumps({"type": "text", "data": "hi"}), buf)
        tr.feed_line(
            json.dumps(
                {
                    "type": "end",
                    "stopReason": "end_turn",
                    "sessionId": "g-1",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "num_turns": 1,
                    "total_cost_usd": 0.01,
                }
            ),
            buf,
        )
        lines = [json.loads(x) for x in buf.getvalue().splitlines() if x]
        types = [x["type"] for x in lines]
        self.assertEqual(types[0], "system")
        self.assertEqual(lines[0]["subtype"], "init")
        self.assertIn("Read", lines[0]["tools"])
        self.assertIn("assistant", types)
        self.assertEqual(types[-1], "result")
        self.assertEqual(tr.grok_session_id, "g-1")

    def test_tool_call(self):
        tr = StreamTranslator("sess", "sonnet", "/tmp", include_partial=False)
        buf = io.StringIO()
        tr.feed_line(
            json.dumps(
                {
                    "type": "tool_call",
                    "toolCallId": "c1",
                    "toolName": "run_terminal_command",
                    "rawInput": {"command": "ls"},
                }
            ),
            buf,
        )
        tr.feed_line(
            json.dumps(
                {
                    "type": "tool_call_update",
                    "toolCallId": "c1",
                    "status": "completed",
                    "rawOutput": "ok",
                }
            ),
            buf,
        )
        events = [json.loads(x) for x in buf.getvalue().splitlines() if x]
        asst = next(e for e in events if e["type"] == "assistant")
        self.assertEqual(asst["message"]["content"][0]["name"], "Bash")
        user = next(e for e in events if e["type"] == "user")
        self.assertEqual(user["message"]["content"][0]["tool_use_id"], "c1")

    def test_extract_user_text(self):
        ev = {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        }
        self.assertEqual(extract_user_text(ev), "hello")


class AuthTests(unittest.TestCase):
    def test_status_logged_in_from_grok_auth(self):
        payload = status_payload()
        self.assertIn("loggedIn", payload)
        self.assertIn("authMethod", payload)

    def test_auth_status_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                handle_auth_argv(["auth", "status"])
        self.assertEqual(ctx.exception.code, 0)

    def test_non_auth_returns_false(self):
        self.assertFalse(handle_auth_argv(["-p", "hello"]))

    def test_usage_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                handle_auth_argv(["--dangerously-skip-permissions", "/usage"])
        self.assertEqual(ctx.exception.code, 0)

    def test_resume_parse(self):
        req = cm.parse(["--resume", "49a8e8ad-5d04-4c14-8cba-2e58e45a6009", "-p", "next"])
        self.assertEqual(req.resume, "49a8e8ad-5d04-4c14-8cba-2e58e45a6009")
        self.assertEqual(req.prompt, "next")
        emit, grok, is_resume = cm.plan_session(req)
        self.assertTrue(is_resume)
        self.assertEqual(emit, req.resume)

    def test_mcp_config_captured(self):
        req = cm.parse(["-p", "x", "--mcp-config", "/tmp/yume-mcp.json"])
        self.assertEqual(req.mcp_config, "/tmp/yume-mcp.json")

    def test_probe_slash(self):
        self.assertTrue(cm._is_probe_slash("/context"))
        self.assertTrue(cm._is_probe_slash("/usage"))
        self.assertFalse(cm._is_probe_slash("keep going"))

    def test_api_mask_detects_yume(self):
        req = cm.parse(
            ["-p", "hi", "--output-format", "stream-json", "--print", "--include-partial-messages"]
        )
        self.assertTrue(cm.is_api_mask(req, ["-p", "hi", "--output-format", "stream-json", "--print"]))

    def test_rewrite_body_fable(self):
        out = json.loads(rewrite_body(b'{"model":"claude-fable-5[1m]","max_tokens":8}'))
        self.assertEqual(out["model"], "grok-4.7")

    def test_sanitize_required_null(self):
        data = sanitize_schema(
            {
                "tools": [
                    {
                        "name": "Read",
                        "input_schema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": None,
                        },
                    }
                ]
            }
        )
        self.assertEqual(data["tools"][0]["input_schema"]["required"], [])

    def test_restamp_model(self):
        buf = b'{"type":"message_start","message":{"model":"grok-4.6"}}'
        out = restamp_model(buf, "claude-haiku-4-5")
        self.assertIn(b"claude-haiku-4-5", out)
        self.assertNotIn(b"grok-4.6", out)

    def test_sanitize_system_role(self):
        data = sanitize_messages(
            {
                "messages": [
                    {"role": "system", "content": "be brief"},
                    {"role": "user", "content": "hi"},
                ]
            }
        )
        roles = [m["role"] for m in data["messages"]]
        self.assertEqual(roles, ["user"])
        self.assertIn("be brief", data["system"])

    def test_sanitize_tool_role(self):
        data = sanitize_messages(
            {
                "messages": [
                    {"role": "user", "content": "run it"},
                    {
                        "role": "tool",
                        "tool_use_id": "t1",
                        "content": "ok",
                    },
                ]
            }
        )
        self.assertEqual(data["messages"][-1]["role"], "user")
        types = []
        for msg in data["messages"]:
            c = msg["content"]
            if isinstance(c, list):
                types.extend(b.get("type") for b in c if isinstance(b, dict))
        self.assertIn("tool_result", types)

    def test_yume_rules_rewrite(self):
        raw = "NEVER use the built-in Bash. always use the RunBash tool (mcp__yume__RunBash)."
        out = cm.rewrite_yume_rules(raw)
        self.assertIn("run_terminal_command", out)


if __name__ == "__main__":
    unittest.main()
