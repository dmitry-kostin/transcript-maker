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
const transcriptSection = document.getElementById("transcript-section");
const transcriptTitle = document.getElementById("transcript-title");
const transcriptEl = document.getElementById("transcript");
const errorEl = document.getElementById("error");
const copyBtn = document.getElementById("copy-btn");
const downloadBtn = document.getElementById("download-btn");
const historyList = document.getElementById("history-list");
const toastEl = document.getElementById("toast");
const cleanupBtn = document.getElementById("cleanup-btn");

let abortController = null;
let pollTimer = null;
let lastHistoryIds = "";

// ─── SVG icons ───

const ICONS = {
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
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

  // Error
  if (newState === AppState.ERROR && data.message) {
    errorEl.hidden = false;
    errorEl.textContent = data.message;
  }

  // Done — show transcript with delay for bloom
  if (newState === AppState.DONE && data.text) {
    setTimeout(() => {
      transcriptEl.textContent = data.text;
      if (data.title) {
        transcriptTitle.textContent = data.title;
      }
      transcriptSection.classList.add("show");
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          transcriptSection.classList.add("visible");
        });
      });
    }, 800);
  }
}

function resetUI() {
  progressEl.hidden = true;
  progressStatus.textContent = "";
  waveformEl.className = "waveform stopped";
  transcriptSection.classList.remove("show", "visible");
  transcriptEl.textContent = "";
  transcriptTitle.textContent = "Transcript";
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

  for (let i = 0; i < bars.length; i++) {
    if (i < completedBars) {
      bars[i].style.background = "var(--green)";
      bars[i].style.animation = "none";
      bars[i].style.opacity = "0.8";
      const center = (BAR_COUNT - 1) / 2;
      const dist = Math.abs(i - center) / center;
      const h = 12 + 44 * (1 - dist * dist);
      bars[i].style.height = `${h.toFixed(1)}px`;
    }
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

// ─── Transcription ───

async function startTranscription(url) {
  resetUI();
  initWaveform();
  setState(AppState.DOWNLOADING, { message: "Downloading audio..." });
  abortController = new AbortController();

  try {
    const diarize = document.getElementById("diarize-toggle").checked;
    const response = await fetch("/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, model: diarize ? "gpt-4o-transcribe-diarize" : "" }),
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
    if (currentState !== AppState.DONE && currentState !== AppState.ERROR) {
      transcribeBtn.hidden = false;
      cancelBtn.hidden = true;
      urlInput.disabled = false;
    } else {
      transcribeBtn.hidden = false;
      cancelBtn.hidden = true;
      urlInput.disabled = false;
    }
    loadHistory();
  }
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
      setState(AppState.DONE, {
        text: data.text,
        title: data.title || "Transcript",
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
  if (!isYouTubeUrl(url)) {
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

copyBtn.addEventListener("click", async () => {
  await navigator.clipboard.writeText(transcriptEl.textContent);
  copyBtn.classList.add("copied");
  const label = copyBtn.querySelector("span");
  const originalText = label.textContent;
  label.textContent = "Copied!";
  showToast("Copied to clipboard");
  setTimeout(() => {
    copyBtn.classList.remove("copied");
    label.textContent = originalText;
  }, 1500);
});

downloadBtn.addEventListener("click", () => {
  const blob = new Blob([transcriptEl.textContent], { type: "text/plain" });
  const a = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  a.href = objectUrl;
  a.download = "transcript.txt";
  a.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
});

// Scroll fade mask for transcript body
transcriptEl.addEventListener("scroll", () => {
  const el = transcriptEl;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20;
  el.classList.toggle("scrolled-bottom", atBottom);
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
  if (!records.length) {
    historyList.innerHTML = '<p class="history-empty">No transcriptions yet.</p>';
    return;
  }

  historyList.innerHTML = records
    .map((r, i) => {
      const date = r.created_at ? new Date(r.created_at + "Z").toLocaleDateString() : "";
      const dur = formatDuration(r.duration);
      const statusLabel = r.status === "in_progress" ? "in progress" : r.status;
      const meta = [statusLabel, dur, date].filter(Boolean).join(" \u00b7 ");

      let actions = "";
      if (r.status === "done") {
        actions = `
          <button onclick="revealInFinder('${r.id}')" title="Show in Finder">${ICONS.folder} Finder</button>
          <button class="delete-btn" onclick="deleteRecord('${r.id}')" title="Delete">${ICONS.trash}</button>`;
      } else if (r.status === "error") {
        actions = `<button class="delete-btn" onclick="deleteRecord('${r.id}')" title="Delete">${ICONS.trash}</button>`;
      }

      return `
        <div class="history-card" style="animation-delay: ${(i * 0.05).toFixed(2)}s">
          <span class="status-dot ${r.status}"></span>
          <div class="card-content">
            <div class="card-title">${escapeHtml(r.title)}</div>
            <div class="card-meta">${meta}${r.status === "error" && r.error ? " \u00b7 " + escapeHtml(r.error) : ""}</div>
          </div>
          <div class="card-actions">${actions}</div>
        </div>`;
    })
    .join("");
}

async function revealInFinder(id) {
  await fetch(`/api/history/${id}/reveal`, { method: "POST" });
}

async function deleteRecord(id) {
  const res = await fetch(`/api/history/${id}`, { method: "DELETE" });
  if (res.ok) {
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

initWaveform();
loadHistory();
