# claude-masked

[![CI](https://github.com/Eris-Margeta/claude-masked/actions/workflows/ci.yml/badge.svg)](https://github.com/Eris-Margeta/claude-masked/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-8cff6a?labelColor=0b0d0c)](LICENSE)
[![Grok 4.7](https://img.shields.io/badge/flagship-grok--4.7-8cff6a?labelColor=0b0d0c)](https://claude-masked.vercel.app/)
[![Site](https://img.shields.io/badge/site-claude--masked.vercel.app-c44a2a?labelColor=0b0d0c)](https://claude-masked.vercel.app/)

**Claude Code harness in name. Grok Build in fact.**

Anything that shells out to `claude` is translated and `exec`’d as `grok`. No Anthropic session. Flagship aliases (Fable and the rest of the ceiling) map to **grok-4.7**. Lesser model names map to grok-4.6.

Site: [claude-masked.vercel.app](https://claude-masked.vercel.app/)

## Why

A lot of local tooling still assumes the Claude Code CLI: version probes, `stream-json` frontends, `claude auth login`. We were already on Grok. This adapter keeps the `claude` surface so those tools keep working, and sends the work to xAI.

## Quick start

```bash
git clone https://github.com/Eris-Margeta/claude-masked.git
cd claude-masked
./install.sh
export PATH="$HOME/.local/bin:$PATH"
claude --version
# 2.1.266 (Claude Code)
# via claude-masked → grok
```

Requires [Grok Build](https://grok.com) (`grok` on PATH) and Python 3.10+.

## Map

| Caller thinks | What runs |
|---|---|
| `claude` | `grok` |
| `claude -p "fix the tests"` | `grok -p "fix the tests"` |
| `--model fable` / `claude-fable-5*` | `--model grok-4.7` |
| `--model sonnet` / opus / haiku | `--model grok-4.6` |
| `--output-format stream-json` | Grok `streaming-json` rewritten to Claude Code NDJSON |
| `claude auth status` | logged in when `~/.grok/auth.json` or `XAI_API_KEY` is valid |
| `claude auth login` | `grok login` |

## Environment

| Variable | Meaning |
|---|---|
| `CLAUDE_MASKED_GROK` | Path to the grok binary |
| `CLAUDE_MASKED_FLAGSHIP` | Default `grok-4.7` |
| `CLAUDE_MASKED_STANDARD` | Default `grok-4.6` |
| `CLAUDE_MASKED_FORCE_MODEL` | Ignore caller `--model` |
| `CLAUDE_MASKED_DEBUG=1` | Print translated argv |

This masks the **CLI harness**. It does not proxy `api.anthropic.com`.

## License

MIT © 2026 [Eris Margeta](mailto:eris.margeta@gmail.com)
