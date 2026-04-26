# Agent Harness JSONL Session Export

This folder contains a small local exporter for agent session JSONL files.

## Web UI

Open `index.html` in a browser to use the local viewer:

- Import one or more `.jsonl` files.
- Drag and drop JSONL files onto the left panel.
- Search sessions and messages.
- Filter by role.
- Toggle raw JSON blocks.
- Export the currently selected session as a standalone HTML file.

All parsing happens in the browser. Files are not uploaded.

## What I found

- Claude Code sessions: `~/.claude/projects/**/*.jsonl`
- Claude Desktop local agent sessions: `~/Library/Application Support/Claude/local-agent-mode-sessions/**/.claude/projects/**/*.jsonl`
- Codex sessions: `~/.codex/sessions/**/*.jsonl`
- Pi Agent sessions: `~/.pi/agent/sessions/**/*.jsonl`
- OpenClaw / Hermes are supported through the generic parser when you pass their JSONL session directory with `--source`.
- Claude Desktop also has app state in `~/Library/Application Support/Claude/IndexedDB`, but the directly useful local agent transcripts found here are JSONL files under the paths above.

The two referenced projects are useful browser viewers:

- `tools/claude-code-jsonl-tracer-site`: static single-page JSONL trace viewer.
- `tools/openclaw-session-browser`: Vite browser app that loads JSONL through the browser File API.

For a batch export, use `agent_jsonl_export.py`.

## Outputs

- `exports/agent-html/index.html`: main sessions only, excluding `subagents/` and `audit.jsonl`.
- `exports/agent-html-all/index.html`: includes `subagents/`, still excludes `audit.jsonl`.

Open either `index.html` in a browser. Each row links to a standalone session HTML page with rendered user/assistant/tool messages and collapsible raw JSON.

## Commands

```bash
python3 agent_jsonl_export.py
python3 agent_jsonl_export.py --include-subagents --output exports/agent-html-all
```

Optional flags:

```bash
python3 agent_jsonl_export.py --source ~/.claude/projects --output exports/claude-code-only
python3 agent_jsonl_export.py --source ~/.codex/sessions --output exports/codex-html
python3 agent_jsonl_export.py --source ~/.pi/agent/sessions --output exports/pi-html
python3 agent_jsonl_export.py --source /path/to/openclaw-or-hermes/sessions --output exports/custom-agent-html
python3 agent_jsonl_export.py --include-subagents --include-audit --output exports/agent-html-with-audit
```

The exporter only reads source folders and writes HTML under the selected output directory. It has explicit adapters for Claude/Pi/Codex shapes plus a generic fallback for JSONL objects with common fields such as `role`, `message`, `content`, `text`, `input`, `output`, `result`, `toolName`, and `arguments`.
