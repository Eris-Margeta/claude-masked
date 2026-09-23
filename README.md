# claude-masked

[![CI](https://github.com/Eris-Margeta/claude-masked/actions/workflows/ci.yml/badge.svg)](https://github.com/Eris-Margeta/claude-masked/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-c5b48a?labelColor=0c0c0b)](LICENSE)
[![Grok 4.7](https://img.shields.io/badge/flagship-grok--4.7-c5b48a?labelColor=0c0c0b)](https://claude-masked.vercel.app/)
[![Site](https://img.shields.io/badge/site-claude--masked.vercel.app-8d8b84?labelColor=0c0c0b)](https://claude-masked.vercel.app/)

Claude Code CLI contract. Grok 4.7 / 4.6 inference.

Site: [claude-masked.vercel.app](https://claude-masked.vercel.app/)

## Install

Requires Python 3.10+ and [Grok Build](https://grok.com) (`grok` on PATH, session in `~/.grok/auth.json`).

```bash
git clone https://github.com/Eris-Margeta/claude-masked.git
cd claude-masked
./install.sh
export PATH="$HOME/.local/bin:$PATH"
claude --version
```

`./uninstall.sh` restores the previous `claude` binaries.

## Modes

| Launch shape | Behaviour |
|---|---|
| `stream-json --print`, MCP config, desktop harnesses | **API mask** — real Claude Code binary; localhost proxy `127.0.0.1:8317` maps models and forwards `/v1/messages` to `api.x.ai`. Sessions and `--resume` stay Claude’s. |
| `claude -p`, interactive PATH | **Harness mask** — argv translated, `exec grok`. |
| `claude auth login` / `auth status` | Grok login / Grok session. |

Force with `CLAUDE_MASKED_MODE=api` or `harness`.

## Model map

| Requested | Routed to |
|---|---|
| fable, best, mythos, `claude-fable-5*` | grok-4.7 |
| opus, sonnet, haiku, other Claude ids | grok-4.6 |

## Environment

| Variable | Meaning |
|---|---|
| `CLAUDE_MASKED_GROK` | Path to the grok binary |
| `CLAUDE_MASKED_REAL_CLAUDE` | Path to the real Claude Code binary |
| `CLAUDE_MASKED_FLAGSHIP` | Default `grok-4.7` |
| `CLAUDE_MASKED_STANDARD` | Default `grok-4.6` |
| `CLAUDE_MASKED_FORCE_MODEL` | Ignore caller `--model` (harness mask) |
| `CLAUDE_MASKED_DEBUG=1` | Log translated argv |
| `CLAUDE_MASKED_MODE` | `api` or `harness` |

## License

MIT © 2026 [Eris Margeta](mailto:eris.margeta@gmail.com)
