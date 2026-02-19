// ─── State machine ───

const AppState = {
  IDLE: "idle",
  DOWNLOADING: "downloading",
  PROCESSING: "processing",
  TRANSCRIBING: "transcribing",
  DONE: "done",
  ERROR: "error",
  CANCELLED: "cancelled",
};

let currentState = AppState.IDLE;

// ─── DOM refs ───

const app = document.querySelector(".app");
const urlInput = document.getElementById("url-input");
const transcribeBtn = document.getElementById("transcribe-btn");
const cancelBtn = document.getElementById("cancel-btn");
const progressEl = document.getElementById("progress");
const waveformEl = document.getElementById("waveform");
const progressStatus = document.getElementById("progress-status");
const activeResultEl = document.getElementById("active-result");
const errorEl = document.getElementById("error");
const historyList = document.getElementById("history-list");
const toastEl = document.getElementById("toast");
const cleanupBtn = document.getElementById("cleanup-btn");

let abortController = null;
let pollTimer = null;
let lastHistoryIds = "";
let activeRecordId = null;
const bodyCache = new Map();

// ─── SVG icons ───

const ICONS = {
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
};

// ─── Helpers ───

function isYouTubeUrl(url) {
  try {
    const u = new URL(url);
    const validHosts = ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"];
    if (!validHosts.includes(u.hostname)) return false;
    if (u.hostname === "youtu.be") return /^\/[\w-]{11}$/.test(u.pathname);
    return u.searchParams.has("v") || /^\/(shorts|live)\/[\w-]{11}/.test(u.pathname);
  } catch {
    return false;
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// ─── State management ───

function setState(newState, data = {}) {
  currentState = newState;

  // Layout state: idle vs active
  const isIdle = newState === AppState.IDLE;
  app.dataset.state = isIdle ? "idle" : "active";

  // Processing controls
  const isActive = [AppState.DOWNLOADING, AppState.PROCESSING, AppState.TRANSCRIBING].includes(newState);
  transcribeBtn.hidden = isActive;
  cancelBtn.hidden = !isActive;
  urlInput.disabled = isActive;

  // Progress section
  if (isActive) {
    progressEl.hidden = false;
  }

  // Waveform
  updateWaveform(newState, data);

  // Status text
  if (data.message) {
    progressStatus.textContent = data.message;
  }

  // Error — show banner, clear status text
  if (newState === AppState.ERROR && data.message) {
    progressStatus.textContent = "";
    errorEl.hidden = false;
    errorEl.textContent = data.message;
  }

  // Done — render active result card with delay for bloom
  if (newState === AppState.DONE && data.text) {
    bodyCache.set(activeRecordId, data.text);
    setTimeout(() => {
      renderActiveResult(data);
    }, 800);
  }
}

function resetUI() {
  progressEl.hidden = true;
  progressStatus.textContent = "";
  waveformEl.className = "waveform stopped";
  activeResultEl.innerHTML = "";
  activeRecordId = null;
  errorEl.hidden = true;
  errorEl.textContent = "";
}

// ─── Waveform controller ───

const BAR_COUNT = 48;

function initWaveform() {
  waveformEl.innerHTML = "";
  for (let i = 0; i < BAR_COUNT; i++) {
    const bar = document.createElement("div");
    bar.className = "bar";
    // Set random peak heights for organic feel
    const peak = 40 + Math.random() * 16; // 40-56px
    bar.style.setProperty("--peak", `${peak}px`);
    bar.style.height = "6px";
    waveformEl.appendChild(bar);
  }
  waveformEl.className = "waveform stopped";
}

function updateWaveform(state, data = {}) {
  const bars = waveformEl.children;

  if (state === AppState.DOWNLOADING) {
    waveformEl.className = "waveform downloading";
    for (let i = 0; i < bars.length; i++) {
      // Left-to-right cascade
      bars[i].style.animationDelay = `${(i * (2.8 / BAR_COUNT)).toFixed(3)}s`;
      const peak = 40 + Math.random() * 16;
      bars[i].style.setProperty("--peak", `${peak}px`);
    }
  } else if (state === AppState.PROCESSING || state === AppState.TRANSCRIBING) {
    waveformEl.className = "waveform transcribing";
    const center = (BAR_COUNT - 1) / 2;
    for (let i = 0; i < bars.length; i++) {
      // Center-outward ripple
      const dist = Math.abs(i - center);
      bars[i].style.animationDelay = `${(dist * 0.06).toFixed(3)}s`;
      // Taller bars at center
      const peak = 56 - (dist / center) * 24;
      bars[i].style.setProperty("--peak", `${peak}px`);
    }
  } else if (state === AppState.DONE) {
    waveformEl.className = "waveform done";
    const center = (BAR_COUNT - 1) / 2;
    for (let i = 0; i < bars.length; i++) {
      const dist = Math.abs(i - center) / center;
      const h = 12 + 44 * (1 - dist * dist); // Parabolic arc
      bars[i].style.height = `${h.toFixed(1)}px`;
      bars[i].style.animationDelay = `${(i * 0.015).toFixed(3)}s`;
    }
  } else if (state === AppState.ERROR) {
    waveformEl.className = "waveform error";
  } else if (state === AppState.CANCELLED || state === AppState.IDLE) {
    waveformEl.className = "waveform stopped";
  }
}

function updateChunkProgress(current, total) {
  const bars = waveformEl.children;
  const barsPerChunk = Math.floor(BAR_COUNT / total);
  const completedBars = current * barsPerChunk;

  const center = (BAR_COUNT - 1) / 2;
  for (let i = 0; i < bars.length; i++) {
    if (i < completedBars) {
      bars[i].style.background = "var(--green)";
      bars[i].style.animation = "none";
      bars[i].style.opacity = "0.8";
      const dist = Math.abs(i - center) / center;
      const h = 56 - dist * 24;
      bars[i].style.height = `${h.toFixed(1)}px`;
    }
  }

  // Transition bars: blend static green into pulsing blue
  const transitionBars = 3;
  for (let i = completedBars; i < Math.min(completedBars + transitionBars, bars.length); i++) {
    const t = (i - completedBars) / transitionBars;
    const minH = 10 + (1 - t) * 20;
    bars[i].style.setProperty("--min", `${minH.toFixed(1)}px`);
  }
}

// ─── Toast ───

let toastTimer = null;

function showToast(msg) {
  if (toastTimer) clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.hidden = false;
  toastEl.classList.remove("active");
  void toastEl.offsetWidth;
  toastEl.classList.add("active");
  toastTimer = setTimeout(() => {
    toastEl.classList.remove("active");
    setTimeout(() => { toastEl.hidden = true; }, 200);
  }, 1500);
}

// ─── Unified card renderer ───

function renderCard(record, opts = {}) {
  const expanded = opts.expanded || false;
  const date = record.created_at ? new Date(record.created_at + "Z").toLocaleDateString() : "";
  const dur = formatDuration(record.duration);
  const statusLabel = record.status === "in_progress" ? "in progress" : record.status;
  const modelLabel = record.model === "gpt-4o-transcribe-diarize" ? "speakers" : "";
  const wordsLabel = record.words ? `${record.words} words` : "";
  const meta = [statusLabel, modelLabel, dur, wordsLabel, date].filter(Boolean).join(" \u00b7 ");

  let actions = "";
  let quickCopy = "";
  if (record.status === "done") {
    quickCopy = `<button class="quick-copy" onclick="event.stopPropagation(); copyRecordText('${record.id}')" title="Copy">${ICONS.copy} Copy</button>`;
    actions = `
      <button onclick="event.stopPropagation(); downloadRecordText('${record.id}', '${escapeHtml(record.title)}')" title="Download .txt">${ICONS.download} .txt</button>
      <button onclick="event.stopPropagation(); revealInFinder('${record.id}')" title="Show in Finder">${ICONS.folder} Finder</button>
      <button onclick="event.stopPropagation(); retranscribe('${record.id}')" title="Re-transcribe">${ICONS.refresh} Re-transcribe</button>
      <button class="delete-btn" onclick="event.stopPropagation(); deleteRecord('${record.id}')" title="Delete">${ICONS.trash} Delete</button>`;
  } else if (record.status === "error") {
    actions = `
      <button onclick="event.stopPropagation(); retranscribe('${record.id}')" title="Retry">${ICONS.refresh} Retry</button>
      <button class="delete-btn" onclick="event.stopPropagation(); deleteRecord('${record.id}')" title="Delete">${ICONS.trash} Delete</button>`;
  }

  const expandable = record.status === "done" ? "expandable" : "";
  const expandedClass = expanded ? "expanded" : "";

  let bodyHtml = "";
  if (expanded && opts.bodyText) {
    bodyHtml = `<div class="card-body" onclick="event.stopPropagation()">${escapeHtml(opts.bodyText)}</div>`;
  }

  return `
    <div class="history-card ${expandable} ${expandedClass}" data-id="${record.id}" data-status="${record.status}" onclick="handleCardClick(this)">
      <div class="card-content">
        <div class="card-title">${escapeHtml(record.title)}</div>
        <div class="card-meta"><span class="status-dot ${record.status}"></span>${meta}${record.status === "error" && record.error ? " \u00b7 " + escapeHtml(record.error) : ""}</div>
        ${actions ? `<div class="card-actions">${actions}</div>` : ""}
      </div>
      ${quickCopy}
      ${bodyHtml}
    </div>`;
}

// ─── Active result slot ───

function renderActiveResult(data) {
  if (!activeRecordId) return;

  const text = data.text || "";
  const record = {
    id: activeRecordId,
    title: data.title || "Transcript",
    status: "done",
    duration: data.duration_seconds || 0,
    model: "",
    words: text.split(/\s+/).filter(Boolean).length,
    created_at: "",
  };

  activeResultEl.innerHTML = renderCard(record, {
    expanded: true,
    bodyText: data.text,
  });

  // Force history to re-render (filters out active record)
  lastHistoryIds = "";
  loadHistory();
}

// ─── New action functions ───

async function copyRecordText(id) {
  const text = await getRecordBody(id);
  if (!text) return;
  await navigator.clipboard.writeText(text);
  showToast("Copied to clipboard");
}

async function downloadRecordText(id, title) {
  const text = await getRecordBody(id);
  if (!text) return;
  const blob = new Blob([text], { type: "text/plain" });
  const a = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  a.href = objectUrl;
  const filename = (title || "transcript").replace(/[^a-zA-Z0-9_\- ]/g, "").trim() || "transcript";
  a.download = `${filename}.txt`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
}

async function getRecordBody(id) {
  if (bodyCache.has(id)) return bodyCache.get(id);
  const res = await fetch(`/api/history/${id}`);
  if (!res.ok) return null;
  const data = await res.json();
  const body = data.body || "";
  bodyCache.set(id, body);
  return body;
}

// ─── Transcription ───

async function runSSE(fetchUrl, fetchBody) {
  resetUI();
  initWaveform();
  setState(AppState.DOWNLOADING, { message: "Downloading audio..." });
  abortController = new AbortController();

  try {
    const response = await fetch(fetchUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fetchBody),
      signal: abortController.signal,
    });

    if (!response.ok) {
      if (response.status === 422) {
        try {
          const body = await response.json();
          const msg = body?.detail?.[0]?.msg || "Invalid request";
          throw new Error(msg.replace(/^Value error,\s*/i, ""));
        } catch (e) {
          if (e instanceof Error && e.message !== "Invalid request") throw e;
        }
      }
      throw new Error(`Server error: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      let currentEvent = null;
      for (const line of lines) {
        if (line.startsWith("event:")) {
          currentEvent = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          const raw = line.slice(5).trim();
          if (!raw) continue;
          const data = JSON.parse(raw);
          handleEvent(currentEvent, data);
        }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      setState(AppState.ERROR, { message: err.message });
    }
  } finally {
    abortController = null;
    transcribeBtn.hidden = false;
    cancelBtn.hidden = true;
    urlInput.disabled = false;
    loadHistory();
  }
}

const DEMO_MODE = new URLSearchParams(window.location.search).has("demo");
const apiPrefix = DEMO_MODE ? "/api/demo" : "/api";

async function startTranscription(url) {
  const diarize = document.getElementById("diarize-toggle").checked;
  runSSE(`${apiPrefix}/transcribe`, { url, model: diarize ? "gpt-4o-transcribe-diarize" : "" });
}

function retranscribe(id) {
  const diarize = document.getElementById("diarize-toggle").checked;
  runSSE(`${apiPrefix}/history/${id}/retranscribe`, { model: diarize ? "gpt-4o-transcribe-diarize" : "" });
}

function handleEvent(event, data) {
  switch (event) {
    case "progress":
      if (data.stage === "downloading") {
        setState(AppState.DOWNLOADING, { message: data.message });
      } else if (data.stage === "processing") {
        setState(AppState.PROCESSING, { message: data.message });
      } else if (data.stage === "transcribing") {
        setState(AppState.TRANSCRIBING, { message: data.message });
        // Parse chunk progress
        const match = data.message.match(/chunk (\d+) of (\d+)/);
        if (match) {
          updateChunkProgress(parseInt(match[1]) - 1, parseInt(match[2]));
        }
      }
      break;

    case "transcript":
      activeRecordId = data.record_id || null;
      setState(AppState.DONE, {
        text: data.text,
        title: data.title || "Transcript",
        duration_seconds: data.duration_seconds,
        message: "Done!",
      });
      break;

    case "error":
      setState(AppState.ERROR, { message: data.message });
      break;
  }
}

// ─── Event listeners ───

transcribeBtn.addEventListener("click", () => {
  const url = urlInput.value.trim();
  if (!url) return;
  if (!DEMO_MODE && !isYouTubeUrl(url)) {
    resetUI();
    app.dataset.state = "active";
    errorEl.hidden = false;
    errorEl.textContent = "Please enter a valid YouTube URL.";
    return;
  }
  startTranscription(url);
});

cancelBtn.addEventListener("click", () => {
  if (abortController) {
    abortController.abort();
    setState(AppState.CANCELLED, { message: "Cancelled." });
    transcribeBtn.hidden = false;
    cancelBtn.hidden = true;
    urlInput.disabled = false;
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") transcribeBtn.click();
});

// ─── History ───

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    if (!res.ok) return;
    const records = await res.json();

    const currentIds = records.map((r) => r.id + r.status).join(",");
    if (currentIds !== lastHistoryIds) {
      lastHistoryIds = currentIds;
      bodyCache.clear();
      renderHistory(records);
    }

    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (records.some((r) => r.status === "in_progress")) {
      pollTimer = setTimeout(loadHistory, 3000);
    }
  } catch {
    // Silently ignore
  }
}

function renderHistory(records) {
  // Filter out the active result record
  const filtered = activeRecordId
    ? records.filter((r) => r.id !== activeRecordId)
    : records;

  if (!filtered.length) {
    historyList.innerHTML = '<p class="history-empty">No transcriptions yet.</p>';
    return;
  }

  historyList.innerHTML = filtered
    .map((r, i) => {
      const html = renderCard(r);
      // Inject animation delay
      return html.replace('onclick="handleCardClick(this)"', `onclick="handleCardClick(this)" style="animation-delay: ${(i * 0.05).toFixed(2)}s"`);
    })
    .join("");
}

async function handleCardClick(cardEl) {
  if (cardEl.dataset.status !== "done") return;
  const id = cardEl.dataset.id;
  await toggleCardBody(id, cardEl);
}

async function toggleCardBody(id, cardEl) {
  const existing = cardEl.querySelector(".card-body");
  const top = cardEl.getBoundingClientRect().top;
  if (existing) {
    existing.remove();
    cardEl.classList.remove("expanded");
  } else {
    const body = await getRecordBody(id);
    if (!body) return;
    const div = document.createElement("div");
    div.className = "card-body";
    div.textContent = body;
    div.addEventListener("click", (e) => e.stopPropagation());
    cardEl.appendChild(div);
    cardEl.classList.add("expanded");
  }
  const shift = cardEl.getBoundingClientRect().top - top;
  if (shift) window.scrollBy(0, shift);
}

async function revealInFinder(id) {
  await fetch(`/api/history/${id}/reveal`, { method: "POST" });
}

async function deleteRecord(id) {
  const res = await fetch(`/api/history/${id}`, { method: "DELETE" });
  if (res.ok) {
    // If deleting the active result, clear it
    if (id === activeRecordId) {
      activeResultEl.innerHTML = "";
      activeRecordId = null;
    }
    lastHistoryIds = "";
    loadHistory();
  }
}

// ─── Cleanup ───

cleanupBtn.addEventListener("click", async () => {
  if (!confirm("Delete all temporary files and clean up stale records?")) return;
  cleanupBtn.disabled = true;
  cleanupBtn.textContent = "Cleaning...";
  try {
    const res = await fetch("/api/cleanup", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      cleanupBtn.textContent = `Done! ${data.deleted_files} files, ${data.cleaned_records} records`;
      setTimeout(() => {
        cleanupBtn.textContent = "Clean up temp files";
        cleanupBtn.disabled = false;
      }, 2500);
      lastHistoryIds = "";
      loadHistory();
    } else {
      cleanupBtn.textContent = "Failed";
      setTimeout(() => {
        cleanupBtn.textContent = "Clean up temp files";
        cleanupBtn.disabled = false;
      }, 2000);
    }
  } catch {
    cleanupBtn.textContent = "Clean up temp files";
    cleanupBtn.disabled = false;
  }
});

// ─── Init ───

if (DEMO_MODE) {
  const badge = document.createElement("div");
  badge.className = "demo-badge";
  badge.textContent = "Demo Mode";
  document.body.appendChild(badge);
}

initWaveform();
loadHistory();
