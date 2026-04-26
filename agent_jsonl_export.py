#!/usr/bin/env python3
"""Export local agent harness JSONL sessions to readable HTML.

This script is read-only against source data directories. It writes a static
HTML index plus one HTML file per JSONL session under the chosen output folder.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


HOME = Path.home()
DEFAULT_SOURCES = [
    HOME / ".claude" / "projects",
    HOME / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions",
    HOME / "Library" / "Application Support" / "Claude" / "claude-code-sessions",
    HOME / ".codex" / "sessions",
    HOME / ".pi" / "agent" / "sessions",
    HOME / ".openclaw" / "sessions",
    HOME / ".hermes" / "sessions",
]


@dataclass
class Message:
    role: str
    timestamp: str | None
    text: str
    details: list[str]
    raw: dict[str, Any]


@dataclass
class Session:
    path: Path
    session_id: str
    harness: str
    title: str
    cwd: str | None
    first_ts: str | None
    last_ts: str | None
    messages: list[Message]
    event_count: int
    parse_errors: int
    size_bytes: int
    html_name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export local agent harness JSONL sessions to static HTML."
    )
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        help="Source file or directory to scan. Can be passed multiple times. Defaults to local Claude paths.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("exports/agent-html"),
        help="Output directory for HTML files. Default: exports/agent-html",
    )
    parser.add_argument(
        "--include-subagents",
        action="store_true",
        help="Include JSONL files under subagents/ directories.",
    )
    parser.add_argument(
        "--include-audit",
        action="store_true",
        help="Include audit.jsonl files.",
    )
    return parser.parse_args()


def iter_jsonl_files(sources: Iterable[Path], include_subagents: bool, include_audit: bool) -> list[Path]:
    found: dict[Path, None] = {}
    for source in sources:
        source = source.expanduser()
        if source.is_file() and source.suffix == ".jsonl":
            candidates = [source]
        elif source.is_dir():
            candidates = list(source.rglob("*.jsonl"))
        else:
            continue

        for path in candidates:
            parts = set(path.parts)
            if not include_subagents and "subagents" in parts:
                continue
            if not include_audit and path.name == "audit.jsonl":
                continue
            found[path.resolve()] = None
    return sorted(found, key=lambda p: str(p))


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    errors = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue
            if isinstance(obj, dict):
                events.append(obj)
    return events, errors


def clean_text(value: str) -> str:
    replacements = {
        "<command-message>": "",
        "</command-message>": "",
        "<command-name>": "Command: ",
        "</command-name>": "",
        "<command-args>": " Args: ",
        "</command-args>": "",
        "<local-command-stdout>": "",
        "</local-command-stdout>": "",
        "<local-command-stderr>": "",
        "</local-command-stderr>": "",
        "<local-command-caveat>": "",
        "</local-command-caveat>": "",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value.strip()


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, indent=2)


def first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def content_to_text_and_details(content: Any) -> tuple[str, list[str]]:
    details: list[str] = []
    chunks: list[str] = []

    if isinstance(content, str):
        return clean_text(content), details
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                chunks.append(clean_text(part))
                continue
            if not isinstance(part, dict):
                chunks.append(stringify(part))
                continue

            kind = part.get("type", "object")
            if kind in {"text", "input_text", "output_text", "summary_text"}:
                chunks.append(clean_text(stringify(first_present(part, "text", "content", "message"))))
            elif kind == "thinking":
                details.append("Thinking\n" + stringify(part.get("thinking")))
            elif kind in {"tool_use", "toolCall", "tool_call", "function_call"}:
                name = first_present(part, "name", "toolName", "function", "callable") or "tool"
                tool_id = part.get("id")
                tool_input = stringify(first_present(part, "input", "arguments", "args", "parameters"))
                chunks.append(f"Tool use: {name}" + (f" ({tool_id})" if tool_id else ""))
                if tool_input:
                    details.append(f"Input for {name}\n{tool_input}")
            elif kind in {"tool_result", "toolResult", "function_call_output"}:
                tool_id = part.get("tool_use_id") or part.get("toolCallId") or part.get("id")
                nested_text, nested_details = content_to_text_and_details(
                    first_present(part, "content", "output", "result", "text")
                )
                if nested_text:
                    chunks.append("Tool result" + (f" ({tool_id})" if tool_id else "") + "\n" + nested_text)
                details.extend(nested_details)
            elif kind in {"image", "document"}:
                chunks.append(f"[{kind} attachment]")
            else:
                details.append(f"{kind}\n{stringify(part)}")
        return "\n\n".join(chunk for chunk in chunks if chunk), details

    if isinstance(content, dict):
        # Generic provider envelopes often store the actual text one layer down.
        text_value = first_present(content, "text", "message", "content", "output", "result")
        if text_value is not content and text_value is not None:
            return content_to_text_and_details(text_value)
        return stringify(content), details
    return stringify(content), details


def detect_harness(path: Path, events: list[dict[str, Any]]) -> str:
    path_text = str(path)
    if "/.codex/" in path_text or any(event.get("type") in {"session_meta", "response_item", "turn_context"} for event in events[:20]):
        return "codex"
    if "/.pi/" in path_text or any(event.get("type") in {"model_change", "thinking_level_change"} for event in events[:20]):
        return "pi"
    if "/.claude/" in path_text or "Application Support/Claude" in path_text:
        return "claude"
    if "openclaw" in path_text.lower():
        return "openclaw"
    if "hermes" in path_text.lower():
        return "hermes"
    return "generic"


def message_payload(event: dict[str, Any]) -> tuple[str, str | None, Any, list[str]]:
    event_type = str(event.get("type") or "event")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else None
    msg = event.get("message") if isinstance(event.get("message"), dict) else None
    details: list[str] = []

    # Codex stores most human-visible items in payload envelopes.
    if payload:
        payload_type = str(payload.get("type") or event_type)
        if payload_type == "message":
            return (
                str(payload.get("role") or payload_type),
                first_str(event.get("timestamp"), payload.get("timestamp")),
                payload.get("content"),
                details,
            )
        if payload_type in {"user_message", "agent_message"}:
            role = "user" if payload_type == "user_message" else "assistant"
            return role, first_str(event.get("timestamp"), payload.get("timestamp")), payload.get("message"), details
        if payload_type in {"tool_call", "function_call", "tool_result", "function_call_output"}:
            return payload_type, first_str(event.get("timestamp"), payload.get("timestamp")), payload, details
        if payload_type in {"session_meta", "turn_context"}:
            return payload_type, first_str(event.get("timestamp"), payload.get("timestamp")), "", [stringify(payload)]

    # Claude Code and Pi both use type=message with message.role/content.
    if msg:
        role = str(first_present(msg, "role", "type") or event_type)
        timestamp = first_str(event.get("timestamp"), msg.get("timestamp"))
        content = first_present(msg, "content", "text", "message", "output", "result")
        for key in ("toolName", "toolCallId", "command", "isError", "usage", "stopReason"):
            if key in msg and msg[key] is not None:
                details.append(f"{key}\n{stringify(msg[key])}")
        return role, timestamp, content, details

    role = str(first_present(event, "role", "speaker", "author", "source", "type") or event_type)
    timestamp = first_str(event.get("timestamp"), event.get("created_at"), event.get("createdAt"), event.get("time"))
    content = first_present(
        event,
        "content",
        "text",
        "message",
        "prompt",
        "output",
        "result",
        "summary",
        "data",
        "arguments",
        "input",
    )
    return role, timestamp, content, details


def message_from_event(event: dict[str, Any]) -> Message | None:
    event_type = str(event.get("type") or "unknown")
    role, timestamp, content, payload_details = message_payload(event)
    text, content_details = content_to_text_and_details(content)
    details = payload_details + content_details

    if not text and event_type in {"summary", "system", "permission-mode", "file-history-snapshot", "attachment"}:
        if event_type == "summary":
            text = stringify(event.get("summary") or event.get("leafUuid"))
        elif event_type == "permission-mode":
            text = "Permission mode: " + stringify(event.get("mode") or event.get("permissionMode"))
        elif event_type == "attachment":
            text = stringify(event.get("fileName") or event.get("filePath") or event.get("attachments"))
        else:
            text = stringify(event.get("subtype") or event.get("data"))

    if not text and not details:
        return None

    if event.get("isMeta"):
        role = f"{role} meta"
    if event.get("isSidechain"):
        role = f"{role} sidechain"
    if event_type not in {"message", role} and not role.endswith(event_type):
        role = f"{role} / {event_type}"

    return Message(
        role=role,
        timestamp=timestamp,
        text=text,
        details=details,
        raw=event,
    )


def title_from_messages(path: Path, messages: list[Message]) -> str:
    for message in messages:
        if "user" in message.role.lower() and message.text:
            first = " ".join(message.text.split())
            if first:
                return first[:90]
    return path.stem


def event_timestamp(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    msg = event.get("message") if isinstance(event.get("message"), dict) else {}
    return first_str(
        event.get("timestamp"),
        event.get("created_at"),
        event.get("createdAt"),
        event.get("time"),
        payload.get("timestamp"),
        msg.get("timestamp"),
    )


def event_session_id(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return first_str(
        event.get("sessionId"),
        event.get("session_id"),
        event.get("conversationId"),
        event.get("conversation_id"),
        event.get("id"),
        payload.get("sessionId"),
        payload.get("session_id"),
        payload.get("conversationId"),
        payload.get("conversation_id"),
        payload.get("id"),
    )


def event_cwd(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return first_str(event.get("cwd"), payload.get("cwd"), payload.get("workdir"), payload.get("workspace"))


def parse_session(path: Path) -> Session | None:
    events, errors = load_jsonl(path)
    if not events and errors:
        return None

    harness = detect_harness(path, events)
    timestamps = [ts for event in events if (ts := event_timestamp(event))]
    session_id = next((sid for event in events if (sid := event_session_id(event))), path.stem)
    cwd = next((cwd_value for event in events if (cwd_value := event_cwd(event))), None)
    messages = [m for e in events if (m := message_from_event(e))]
    title = title_from_messages(path, messages)
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    html_name = f"{safe_file_name(harness)}-{safe_file_name(path.stem)}-{digest}.html"

    return Session(
        path=path,
        session_id=session_id,
        harness=harness,
        title=title,
        cwd=cwd,
        first_ts=min(timestamps) if timestamps else None,
        last_ts=max(timestamps) if timestamps else None,
        messages=messages,
        event_count=len(events),
        parse_errors=errors,
        size_bytes=path.stat().st_size,
        html_name=html_name,
    )


def safe_file_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "session"


def fmt_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return value


def render_markdownish(text: str) -> str:
    escaped = html.escape(text)
    return escaped.replace("\n", "<br>")


def render_session(session: Session) -> str:
    messages = []
    for index, message in enumerate(session.messages, start=1):
        details_html = "".join(
            f"<details><summary>{html.escape(block.splitlines()[0] if block else 'Details')}</summary>"
            f"<pre>{html.escape(block)}</pre></details>"
            for block in message.details
        )
        raw_json = json.dumps(message.raw, ensure_ascii=False, indent=2)
        messages.append(
            f"""
            <article class="message role-{safe_file_name(message.role.lower())}">
              <header>
                <span class="role">{html.escape(message.role)}</span>
                <span class="time">{html.escape(fmt_time(message.timestamp))}</span>
                <span class="num">#{index}</span>
              </header>
              <div class="body">{render_markdownish(message.text)}</div>
              {details_html}
              <details class="raw"><summary>Raw JSON</summary><pre>{html.escape(raw_json)}</pre></details>
            </article>
            """
        )

    return base_html(
        title=session.title,
        body=f"""
        <nav><a href="index.html">Back to index</a></nav>
        <section class="session-hero">
          <h1>{html.escape(session.title)}</h1>
          <dl>
            <dt>Harness</dt><dd>{html.escape(session.harness)}</dd>
            <dt>Session</dt><dd>{html.escape(session.session_id)}</dd>
            <dt>Source</dt><dd>{html.escape(str(session.path))}</dd>
            <dt>CWD</dt><dd>{html.escape(session.cwd or "")}</dd>
            <dt>Time</dt><dd>{html.escape(fmt_time(session.first_ts))} - {html.escape(fmt_time(session.last_ts))}</dd>
            <dt>Events</dt><dd>{session.event_count} events, {len(session.messages)} rendered messages, {session.parse_errors} parse errors</dd>
          </dl>
        </section>
        <main class="messages">
          {''.join(messages)}
        </main>
        """,
    )


def render_index(sessions: list[Session], sources: list[Path]) -> str:
    rows = []
    for session in sorted(sessions, key=lambda s: s.last_ts or "", reverse=True):
        rows.append(
            f"""
            <tr>
              <td><a href="{html.escape(session.html_name)}">{html.escape(session.title)}</a></td>
              <td>{html.escape(session.harness)}</td>
              <td>{html.escape(fmt_time(session.last_ts))}</td>
              <td>{len(session.messages)}</td>
              <td>{session.event_count}</td>
              <td>{html.escape(session.cwd or "")}</td>
              <td class="path">{html.escape(str(session.path))}</td>
            </tr>
            """
        )

    source_items = "".join(f"<li>{html.escape(str(source.expanduser()))}</li>" for source in sources)
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return base_html(
        title="Agent Session Export",
        body=f"""
        <section class="session-hero">
          <h1>Agent Session Export</h1>
          <p>{len(sessions)} sessions exported on {html.escape(generated_at)}.</p>
          <details><summary>Scanned sources</summary><ul>{source_items}</ul></details>
        </section>
        <main>
          <table>
            <thead>
              <tr><th>Title</th><th>Harness</th><th>Last activity</th><th>Messages</th><th>Events</th><th>CWD</th><th>Source</th></tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </main>
        """,
    )


def base_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f7f4ee; --ink:#1d2430; --muted:#667085; --line:#d8d0c3; --panel:#fffaf1; --user:#e8f4ee; --assistant:#f1eef8; --tool:#eef5fb; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg:#171717; --ink:#f0eee8; --muted:#aaa39a; --line:#393633; --panel:#202020; --user:#183225; --assistant:#282139; --tool:#172838; }} }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    a {{ color:inherit; }}
    nav, main, .session-hero {{ max-width:1180px; margin:0 auto; padding:16px 20px; }}
    nav {{ color:var(--muted); }}
    h1 {{ margin:10px 0 12px; font-size:28px; line-height:1.15; }}
    p {{ color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); }}
    th, td {{ text-align:left; vertical-align:top; border-bottom:1px solid var(--line); padding:10px; }}
    th {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    td.path {{ max-width:360px; overflow-wrap:anywhere; color:var(--muted); font-size:12px; }}
    dl {{ display:grid; grid-template-columns:max-content 1fr; gap:6px 14px; background:var(--panel); border:1px solid var(--line); padding:14px; }}
    dt {{ color:var(--muted); }}
    dd {{ margin:0; overflow-wrap:anywhere; }}
    .messages {{ display:flex; flex-direction:column; gap:12px; }}
    .message {{ border:1px solid var(--line); background:var(--panel); border-radius:8px; overflow:hidden; }}
    .message header {{ display:flex; gap:10px; align-items:center; padding:8px 12px; border-bottom:1px solid var(--line); color:var(--muted); }}
    .message .role {{ color:var(--ink); font-weight:700; text-transform:capitalize; }}
    .message .num {{ margin-left:auto; }}
    .message .body {{ padding:14px 16px; white-space:normal; overflow-wrap:anywhere; }}
    [class*="role-user"] .body {{ background:var(--user); }}
    [class*="role-assistant"] .body, [class*="role-agent-message"] .body {{ background:var(--assistant); }}
    [class*="role-tool"] .body, [class*="role-function"] .body, [class*="role-bash"] .body {{ background:var(--tool); }}
    details {{ margin:10px 16px; }}
    summary {{ cursor:pointer; color:var(--muted); }}
    pre {{ margin:8px 0 0; padding:12px; overflow:auto; background:rgba(127,127,127,.12); border-radius:6px; white-space:pre-wrap; }}
    .raw {{ border-top:1px solid var(--line); padding-top:10px; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    sources = args.source or DEFAULT_SOURCES
    output = args.output.expanduser()
    output.mkdir(parents=True, exist_ok=True)

    files = iter_jsonl_files(sources, args.include_subagents, args.include_audit)
    sessions = [session for path in files if (session := parse_session(path))]

    for session in sessions:
        (output / session.html_name).write_text(render_session(session), encoding="utf-8")
    (output / "index.html").write_text(render_index(sessions, sources), encoding="utf-8")

    print(f"Scanned {len(files)} JSONL files")
    print(f"Exported {len(sessions)} sessions")
    print(f"Index: {output.resolve() / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
