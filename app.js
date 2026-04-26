const state = {
  sessions: [],
  activeIndex: -1,
};

const els = {
  fileInput: document.getElementById("file-input"),
  openButton: document.getElementById("open-button"),
  exportButton: document.getElementById("export-button"),
  status: document.getElementById("status"),
  dropZone: document.getElementById("drop-zone"),
  sessionSearch: document.getElementById("session-search"),
  sessionList: document.getElementById("session-list"),
  emptyState: document.getElementById("empty-state"),
  viewer: document.getElementById("viewer"),
  harnessPill: document.getElementById("harness-pill"),
  sessionTitle: document.getElementById("session-title"),
  sessionMeta: document.getElementById("session-meta"),
  messageSearch: document.getElementById("message-search"),
  roleFilter: document.getElementById("role-filter"),
  rawToggle: document.getElementById("raw-toggle"),
  messageList: document.getElementById("message-list"),
};

els.openButton.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", (event) => importFiles(event.target.files));
els.exportButton.addEventListener("click", exportActiveSession);
els.sessionSearch.addEventListener("input", renderSessionList);
els.messageSearch.addEventListener("input", renderActiveSession);
els.roleFilter.addEventListener("change", renderActiveSession);
els.rawToggle.addEventListener("change", () => {
  els.messageList.classList.toggle("show-raw", els.rawToggle.checked);
});

document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("drop", (event) => {
  event.preventDefault();
  els.dropZone.classList.remove("dragging");
  importFiles(event.dataTransfer.files);
});

els.dropZone.addEventListener("dragenter", () => els.dropZone.classList.add("dragging"));
els.dropZone.addEventListener("dragleave", () => els.dropZone.classList.remove("dragging"));

async function importFiles(fileList) {
  const files = Array.from(fileList || []).filter((file) => /\.(jsonl|json)$/i.test(file.name));
  if (!files.length) return;

  const imported = [];
  for (const file of files) {
    const raw = await file.text();
    imported.push(parseSession(file.name, raw, file.size));
  }

  state.sessions.push(...imported);
  state.activeIndex = state.sessions.length - imported.length;
  els.status.textContent = `${state.sessions.length} session${state.sessions.length === 1 ? "" : "s"} loaded`;
  renderSessionList();
  renderActiveSession();
}

function parseJsonl(raw) {
  const events = [];
  let parseErrors = 0;
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const value = JSON.parse(trimmed);
      if (value && typeof value === "object" && !Array.isArray(value)) {
        events.push(value);
      }
    } catch {
      parseErrors += 1;
    }
  }
  return { events, parseErrors };
}

function parseSession(fileName, raw, sizeBytes) {
  const { events, parseErrors } = parseJsonl(raw);
  const harness = detectHarness(fileName, events);
  const messages = events.map(messageFromEvent).filter(Boolean);
  const timestamps = events.map(eventTimestamp).filter(Boolean);
  const sessionId = events.map(eventSessionId).find(Boolean) || fileName.replace(/\.jsonl?$/i, "");
  const cwd = events.map(eventCwd).find(Boolean) || "";
  const title = titleFromMessages(fileName, messages);

  return {
    fileName,
    raw,
    harness,
    sessionId,
    cwd,
    title,
    firstTs: timestamps.sort()[0] || "",
    lastTs: timestamps.sort().at(-1) || "",
    messages,
    eventCount: events.length,
    parseErrors,
    sizeBytes,
  };
}

function detectHarness(fileName, events) {
  const lower = fileName.toLowerCase();
  if (lower.includes("rollout-") || events.slice(0, 20).some((event) => ["session_meta", "response_item", "turn_context"].includes(event.type))) return "codex";
  if (events.slice(0, 20).some((event) => ["model_change", "thinking_level_change"].includes(event.type))) return "pi";
  if (lower.includes("claude") || events.some((event) => event.message && event.message.api && String(event.message.api).includes("anthropic"))) return "claude";
  if (lower.includes("openclaw")) return "openclaw";
  if (lower.includes("hermes")) return "hermes";
  return "generic";
}

function messageFromEvent(event) {
  const eventType = String(event.type || "event");
  const { role, timestamp, content, details } = messagePayload(event);
  const rendered = contentToTextAndDetails(content);
  let text = rendered.text;
  const allDetails = details.concat(rendered.details);

  if (!text && ["summary", "system", "permission-mode", "file-history-snapshot", "attachment"].includes(eventType)) {
    text = stringify(firstPresent(event, "summary", "leafUuid", "mode", "permissionMode", "fileName", "filePath", "data", "subtype"));
  }

  if (!text && !allDetails.length) return null;

  let finalRole = role || eventType;
  if (event.isMeta) finalRole += " meta";
  if (event.isSidechain) finalRole += " sidechain";
  if (eventType !== "message" && !finalRole.includes(eventType)) finalRole += ` / ${eventType}`;

  return {
    role: finalRole,
    roleClass: roleClass(finalRole),
    timestamp,
    text,
    details: allDetails,
    raw: event,
  };
}

function messagePayload(event) {
  const eventType = String(event.type || "event");
  const payload = isPlainObject(event.payload) ? event.payload : null;
  const msg = isPlainObject(event.message) ? event.message : null;
  const details = [];

  if (payload) {
    const payloadType = String(payload.type || eventType);
    if (payloadType === "message") {
      return { role: String(payload.role || payloadType), timestamp: firstString(event.timestamp, payload.timestamp), content: payload.content, details };
    }
    if (["user_message", "agent_message"].includes(payloadType)) {
      return {
        role: payloadType === "user_message" ? "user" : "assistant",
        timestamp: firstString(event.timestamp, payload.timestamp),
        content: payload.message,
        details,
      };
    }
    if (["tool_call", "function_call", "tool_result", "function_call_output"].includes(payloadType)) {
      return { role: payloadType, timestamp: firstString(event.timestamp, payload.timestamp), content: payload, details };
    }
    if (["session_meta", "turn_context"].includes(payloadType)) {
      return { role: payloadType, timestamp: firstString(event.timestamp, payload.timestamp), content: "", details: [stringify(payload)] };
    }
  }

  if (msg) {
    for (const key of ["toolName", "toolCallId", "command", "isError", "usage", "stopReason"]) {
      if (msg[key] !== undefined && msg[key] !== null) details.push(`${key}\n${stringify(msg[key])}`);
    }
    return {
      role: String(firstPresent(msg, "role", "type") || eventType),
      timestamp: firstString(event.timestamp, msg.timestamp),
      content: firstPresent(msg, "content", "text", "message", "output", "result"),
      details,
    };
  }

  return {
    role: String(firstPresent(event, "role", "speaker", "author", "source", "type") || eventType),
    timestamp: firstString(event.timestamp, event.created_at, event.createdAt, event.time),
    content: firstPresent(event, "content", "text", "message", "prompt", "output", "result", "summary", "data", "arguments", "input"),
    details,
  };
}

function contentToTextAndDetails(content) {
  const details = [];
  const chunks = [];

  if (content === undefined || content === null) return { text: "", details };
  if (typeof content === "string") return { text: cleanText(content), details };
  if (typeof content === "number" || typeof content === "boolean") return { text: String(content), details };

  if (Array.isArray(content)) {
    for (const part of content) {
      if (typeof part === "string") {
        chunks.push(cleanText(part));
        continue;
      }
      if (!isPlainObject(part)) {
        chunks.push(stringify(part));
        continue;
      }
      const kind = part.type || "object";
      if (["text", "input_text", "output_text", "summary_text"].includes(kind)) {
        chunks.push(cleanText(stringify(firstPresent(part, "text", "content", "message"))));
      } else if (kind === "thinking") {
        details.push(`Thinking\n${stringify(part.thinking)}`);
      } else if (["tool_use", "toolCall", "tool_call", "function_call"].includes(kind)) {
        const name = firstPresent(part, "name", "toolName", "function", "callable") || "tool";
        const input = stringify(firstPresent(part, "input", "arguments", "args", "parameters"));
        chunks.push(`Tool use: ${name}${part.id ? ` (${part.id})` : ""}`);
        if (input) details.push(`Input for ${name}\n${input}`);
      } else if (["tool_result", "toolResult", "function_call_output"].includes(kind)) {
        const nested = contentToTextAndDetails(firstPresent(part, "content", "output", "result", "text"));
        if (nested.text) chunks.push(`Tool result${part.id ? ` (${part.id})` : ""}\n${nested.text}`);
        details.push(...nested.details);
      } else if (["image", "document"].includes(kind)) {
        chunks.push(`[${kind} attachment]`);
      } else {
        details.push(`${kind}\n${stringify(part)}`);
      }
    }
    return { text: chunks.filter(Boolean).join("\n\n"), details };
  }

  if (isPlainObject(content)) {
    const nested = firstPresent(content, "text", "message", "content", "output", "result");
    if (nested !== undefined && nested !== content) return contentToTextAndDetails(nested);
  }

  return { text: stringify(content), details };
}

function renderSessionList() {
  const query = els.sessionSearch.value.trim().toLowerCase();
  els.sessionList.innerHTML = "";

  state.sessions.forEach((session, index) => {
    const haystack = `${session.title} ${session.fileName} ${session.harness} ${session.cwd}`.toLowerCase();
    if (query && !haystack.includes(query)) return;

    const button = document.createElement("button");
    button.type = "button";
    button.className = `session-card${index === state.activeIndex ? " active" : ""}`;
    button.innerHTML = `
      <strong>${escapeHtml(session.title)}</strong>
      <p>${escapeHtml(session.harness)} - ${session.messages.length} messages - ${formatTime(session.lastTs)}</p>
      <p>${escapeHtml(session.fileName)}</p>
    `;
    button.addEventListener("click", () => {
      state.activeIndex = index;
      renderSessionList();
      renderActiveSession();
    });
    els.sessionList.appendChild(button);
  });
}

function renderActiveSession() {
  const session = state.sessions[state.activeIndex];
  if (!session) {
    els.emptyState.classList.remove("hidden");
    els.viewer.classList.add("hidden");
    els.exportButton.disabled = true;
    return;
  }

  els.emptyState.classList.add("hidden");
  els.viewer.classList.remove("hidden");
  els.exportButton.disabled = false;
  els.harnessPill.textContent = session.harness;
  els.sessionTitle.textContent = session.title;
  els.sessionMeta.innerHTML = `
    <dt>Session</dt><dd>${escapeHtml(session.sessionId)}</dd>
    <dt>File</dt><dd>${escapeHtml(session.fileName)}</dd>
    <dt>CWD</dt><dd>${escapeHtml(session.cwd || "")}</dd>
    <dt>Time</dt><dd>${escapeHtml(formatTime(session.firstTs))} - ${escapeHtml(formatTime(session.lastTs))}</dd>
    <dt>Events</dt><dd>${session.eventCount} events, ${session.messages.length} messages, ${session.parseErrors} parse errors</dd>
  `;

  updateRoleFilter(session);
  renderMessages(session);
}

function updateRoleFilter(session) {
  const selected = els.roleFilter.value;
  const roles = Array.from(new Set(session.messages.map((message) => message.role))).sort();
  els.roleFilter.innerHTML = `<option value="all">All roles</option>` + roles.map((role) => `<option value="${escapeHtml(role)}">${escapeHtml(role)}</option>`).join("");
  els.roleFilter.value = roles.includes(selected) ? selected : "all";
}

function renderMessages(session) {
  const query = els.messageSearch.value.trim().toLowerCase();
  const role = els.roleFilter.value;
  els.messageList.classList.toggle("show-raw", els.rawToggle.checked);
  els.messageList.innerHTML = "";

  session.messages.forEach((message, index) => {
    const text = `${message.role} ${message.text} ${message.details.join("\n")}`.toLowerCase();
    if (role !== "all" && message.role !== role) return;
    if (query && !text.includes(query)) return;

    const article = document.createElement("article");
    article.className = `message ${message.roleClass}`;
    article.innerHTML = `
      <header>
        <span class="role">${escapeHtml(message.role)}</span>
        <span class="time">${escapeHtml(formatTime(message.timestamp))}</span>
        <span class="index">#${index + 1}</span>
      </header>
      <div class="message-body">${escapeHtml(message.text)}</div>
      ${message.details.map((detail) => `<details><summary>${escapeHtml(firstLine(detail) || "Details")}</summary><pre>${escapeHtml(detail)}</pre></details>`).join("")}
      <details class="raw-json"><summary>Raw JSON</summary><pre>${escapeHtml(JSON.stringify(message.raw, null, 2))}</pre></details>
    `;
    els.messageList.appendChild(article);
  });
}

function exportActiveSession() {
  const session = state.sessions[state.activeIndex];
  if (!session) return;
  const html = buildStandaloneHtml(session);
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${safeFileName(session.title || session.sessionId)}.html`;
  link.click();
  URL.revokeObjectURL(url);
}

function buildStandaloneHtml(session) {
  const messages = session.messages.map((message, index) => `
    <article class="message ${message.roleClass}">
      <header><span class="role">${escapeHtml(message.role)}</span><span class="time">${escapeHtml(formatTime(message.timestamp))}</span><span class="index">#${index + 1}</span></header>
      <div class="message-body">${escapeHtml(message.text)}</div>
      ${message.details.map((detail) => `<details><summary>${escapeHtml(firstLine(detail) || "Details")}</summary><pre>${escapeHtml(detail)}</pre></details>`).join("")}
      <details><summary>Raw JSON</summary><pre>${escapeHtml(JSON.stringify(message.raw, null, 2))}</pre></details>
    </article>
  `).join("");

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(session.title)}</title>
  <style>${styleText(currentCssText())}</style>
</head>
<body>
  <main class="workspace">
    <section class="viewer">
      <div class="session-header">
        <div><div class="pill">${escapeHtml(session.harness)}</div><h2>${escapeHtml(session.title)}</h2></div>
        <dl>
          <dt>Session</dt><dd>${escapeHtml(session.sessionId)}</dd>
          <dt>File</dt><dd>${escapeHtml(session.fileName)}</dd>
          <dt>CWD</dt><dd>${escapeHtml(session.cwd || "")}</dd>
          <dt>Time</dt><dd>${escapeHtml(formatTime(session.firstTs))} - ${escapeHtml(formatTime(session.lastTs))}</dd>
          <dt>Events</dt><dd>${session.eventCount} events, ${session.messages.length} messages, ${session.parseErrors} parse errors</dd>
        </dl>
      </div>
      <div class="message-list show-raw">${messages}</div>
    </section>
  </main>
</body>
</html>`;
}

function currentCssText() {
  const chunks = [];
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      chunks.push(Array.from(sheet.cssRules).map((rule) => rule.cssText).join("\n"));
    } catch {
      // Ignore inaccessible stylesheets. The app ships with local CSS only.
    }
  }
  return chunks.join("\n");
}

function styleText(value) {
  return String(value || "").replace(/<\/style/gi, "<\\/style");
}

function titleFromMessages(fileName, messages) {
  const user = messages.find((message) => message.role.toLowerCase().includes("user") && message.text);
  if (!user) return fileName.replace(/\.jsonl?$/i, "");
  return user.text.replace(/\s+/g, " ").slice(0, 90);
}

function eventTimestamp(event) {
  const payload = isPlainObject(event.payload) ? event.payload : {};
  const msg = isPlainObject(event.message) ? event.message : {};
  return firstString(event.timestamp, event.created_at, event.createdAt, event.time, payload.timestamp, msg.timestamp);
}

function eventSessionId(event) {
  const payload = isPlainObject(event.payload) ? event.payload : {};
  return firstString(event.sessionId, event.session_id, event.conversationId, event.conversation_id, event.id, payload.sessionId, payload.session_id, payload.conversationId, payload.conversation_id, payload.id);
}

function eventCwd(event) {
  const payload = isPlainObject(event.payload) ? event.payload : {};
  return firstString(event.cwd, payload.cwd, payload.workdir, payload.workspace);
}

function firstPresent(object, ...keys) {
  if (!isPlainObject(object)) return undefined;
  for (const key of keys) {
    if (object[key] !== undefined && object[key] !== null) return object[key];
  }
  return undefined;
}

function firstString(...values) {
  return values.find((value) => typeof value === "string" && value) || "";
}

function stringify(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return cleanText(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

function cleanText(value) {
  const replacements = {
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
  };
  let text = value;
  for (const [from, to] of Object.entries(replacements)) text = text.split(from).join(to);
  return text.trim();
}

function roleClass(role) {
  const lower = role.toLowerCase();
  if (lower.includes("user")) return "role-user";
  if (lower.includes("assistant") || lower.includes("agent_message")) return "role-assistant";
  if (lower.includes("tool") || lower.includes("function") || lower.includes("bash")) return "role-tool";
  if (lower.includes("session") || lower.includes("turn_context")) return "role-event";
  return "role-event";
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function firstLine(value) {
  return String(value || "").split(/\r?\n/)[0];
}

function safeFileName(value) {
  return String(value || "session").replace(/[^a-z0-9_-]+/gi, "-").replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, 80) || "session";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
