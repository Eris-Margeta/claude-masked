"""Yume / Claude Code model + tool names → Grok Build."""

from __future__ import annotations

import os
from typing import Optional

GROK_FLAGSHIP = os.environ.get("CLAUDE_MASKED_FLAGSHIP", "grok-4.7")
GROK_STANDARD = os.environ.get("CLAUDE_MASKED_STANDARD", "grok-4.6")

# Flagship (Fable / Mythos / "best") → grok-4.7. Everything else → grok-4.6.
FABLE_KEYS = {
    "fable",
    "best",
    "mythos",
    "claude-fable",
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-mythos",
    "claude-mythos-5",
    "anthropic.claude-fable-5",
    "anthropic.claude-fable-5-1",
}

TOOL_TO_GROK = {
    "Bash": "run_terminal_command",
    "PowerShell": "run_terminal_command",
    "Read": "read_file",
    "Write": "write",
    "Edit": "search_replace",
    "MultiEdit": "search_replace",
    "Glob": "list_dir",
    "LS": "list_dir",
    "Grep": "grep",
    "WebSearch": "web_search",
    "WebFetch": "web_fetch",
    "TodoWrite": "todo_write",
    "Task": "spawn_subagent",
    "Agent": "spawn_subagent",
    "AskUserQuestion": "ask_user_question",
    "NotebookEdit": "write",
}

GROK_TO_CLAUDE_TOOL = {v: k for k, v in TOOL_TO_GROK.items()}
GROK_TO_CLAUDE_TOOL.update(
    {
        "run_terminal_command": "Bash",
        "read_file": "Read",
        "write": "Write",
        "search_replace": "Edit",
        "list_dir": "Glob",
        "grep": "Grep",
        "web_search": "WebSearch",
        "web_fetch": "WebFetch",
        "todo_write": "TodoWrite",
        "spawn_subagent": "Agent",
        "ask_user_question": "AskUserQuestion",
    }
)

# What `claude --list-models` should print so Yume's picker stays populated.
LIST_MODELS = [
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]


def is_fable(name: str) -> bool:
    key = name.strip().lower()
    if key in FABLE_KEYS:
        return True
    if "fable" in key or "mythos" in key:
        return True
    return False


def map_model(name: Optional[str]) -> str:
    forced = os.environ.get("CLAUDE_MASKED_FORCE_MODEL")
    if forced:
        return forced
    if not name:
        return os.environ.get("CLAUDE_MASKED_MODEL") or GROK_STANDARD
    if is_fable(name):
        return GROK_FLAGSHIP
    return GROK_STANDARD


# Yume disables Claude Bash in favor of mcp__yume__RunBash. Grok has no that MCP
# unless configured; stripping Grok's shell would freeze agent loops.
KEEP_GROK_SHELL = {
    "Bash",
    "PowerShell",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
}


def map_tools_csv(value: str) -> str:
    parts = [p.strip() for p in value.replace(" ", ",").split(",") if p.strip()]
    mapped = [TOOL_TO_GROK.get(p, TOOL_TO_GROK.get(p.split("(")[0], p)) for p in parts]
    return ",".join(mapped)


def map_disallowed_csv(value: str) -> str:
    parts = [p.strip() for p in value.replace(" ", ",").split(",") if p.strip()]
    out = []
    for p in parts:
        base = p.split("(")[0]
        if base in KEEP_GROK_SHELL:
            continue
        out.append(TOOL_TO_GROK.get(p, TOOL_TO_GROK.get(base, p)))
    return ",".join(out)


def claude_tool_name(grok_name: str) -> str:
    return GROK_TO_CLAUDE_TOOL.get(grok_name, grok_name)
