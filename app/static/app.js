const urlInput = document.getElementById("url-input");
const transcribeBtn = document.getElementById("transcribe-btn");
const cancelBtn = document.getElementById("cancel-btn");
const progressEl = document.getElementById("progress");
const waveformEl = document.getElementById("waveform");
const progressStatus = document.getElementById("progress-status");
const transcriptSection = document.getElementById("transcript-section");
const transcriptEl = document.getElementById("transcript");
const errorEl = document.getElementById("error");
const copyBtn = document.getElementById("copy-btn");
const downloadBtn = document.getElementById("download-btn");
const historyList = document.getElementById("history-list");
const toastEl = document.getElementById("toast");

let abortController = null;
let pollTimer = null;
let lastHistoryIds = "";

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

// --- Waveform ---

function initWaveform() {
  waveformEl.innerHTML = "";
  for (let i = 0; i < 36; i++) {
    const bar = document.createElement("div");
    bar.className = "bar";
    const h = 0.3 + Math.random() * 0.7;
    bar.style.height = `${h * 100}%`;
    bar.style.animationDelay = `${(i * 0.06).toFixed(2)}s`;
    waveformEl.appendChild(bar);
  }
}

function startWaveform() {
  waveformEl.classList.remove("stopped");
}

function stopWaveform() {
  waveformEl.classList.add("stopped");
}

// --- Toast ---

let toastTimer = null;

function showToast(msg) {
  if (toastTimer) clearTimeout(toastTimer);
  toastEl.textContent = msg;
  toastEl.hidden = false;
  // Force reflow to restart animation
  toastEl.classList.remove("active");
  void toastEl.offsetWidth;
  toastEl.classList.add("active");
  toastTimer = setTimeout(() => {
    toastEl.classList.remove("active");
    setTimeout(() => { toastEl.hidden = true; }, 300);
  }, 1500);
}

// --- Core UI ---

function reset() {
  progressEl.hidden = true;
  progressStatus.textContent = "";
  stopWaveform();
  transcriptSection.classList.remove("show", "visible");
  transcriptEl.textContent = "";
  errorEl.hidden = true;
  errorEl.textContent = "";
}

function setProcessing(active) {
  transcribeBtn.hidden = active;
  cancelBtn.hidden = !active;
  urlInput.disabled = active;
}

function setStatus(msg) {
  progressEl.hidden = false;
  progressStatus.textContent = msg;
}

function showError(msg) {
  errorEl.hidden = false;
  errorEl.textContent = msg;
}

function showTranscript(text) {
  transcriptEl.textContent = text;
  transcriptSection.classList.add("show");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      transcriptSection.classList.add("visible");
    });
  });
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// --- Transcription ---

async function startTranscription(url) {
  reset();
  setProcessing(true);
  initWaveform();
  startWaveform();
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
      showError(err.message);
    }
  } finally {
    abortController = null;
    setProcessing(false);
    stopWaveform();
    loadHistory();
  }
}

function handleEvent(event, data) {
  switch (event) {
    case "progress":
      setStatus(data.message);
      break;
    case "transcript":
      stopWaveform();
      showTranscript(data.text);
      setStatus("Done!");
      break;
    case "error":
      stopWaveform();
      showError(data.message);
      break;
  }
}

transcribeBtn.addEventListener("click", () => {
  const url = urlInput.value.trim();
  if (!url) return;
  if (!isYouTubeUrl(url)) {
    reset();
    showError("Please enter a valid YouTube URL.");
    return;
  }
  startTranscription(url);
});

cancelBtn.addEventListener("click", () => {
  if (abortController) {
    abortController.abort();
    stopWaveform();
    setStatus("Cancelled.");
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") transcribeBtn.click();
});

copyBtn.addEventListener("click", async () => {
  await navigator.clipboard.writeText(transcriptEl.textContent);
  showToast("Copied!");
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

// --- History ---

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    if (!res.ok) return;
    const records = await res.json();

    // Cache check: skip re-render if records haven't changed
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
    // Silently ignore history load failures
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
      const meta = [statusLabel, dur, date].filter(Boolean).join(" · ");

      let actions = "";
      if (r.status === "done") {
        actions = `
          <button onclick="revealInFinder('${r.id}')">Finder</button>
          <button class="delete-btn" onclick="deleteRecord('${r.id}')">Delete</button>`;
      } else if (r.status === "error") {
        actions = `<button class="delete-btn" onclick="deleteRecord('${r.id}')">Delete</button>`;
      }

      return `
        <div class="timeline-item status-${r.status}" style="animation-delay: ${(i * 0.05).toFixed(2)}s">
          <div class="title">${escapeHtml(r.title)}</div>
          <div class="meta">${meta}${r.status === "error" && r.error ? " · " + escapeHtml(r.error) : ""}</div>
          <div class="card-actions">${actions}</div>
        </div>`;
    })
    .join("");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function revealInFinder(id) {
  await fetch(`/api/history/${id}/reveal`, { method: "POST" });
}

async function deleteRecord(id) {
  const res = await fetch(`/api/history/${id}`, { method: "DELETE" });
  if (res.ok) {
    lastHistoryIds = ""; // Force re-render
    loadHistory();
  }
}

// --- Cleanup ---

const cleanupBtn = document.getElementById("cleanup-btn");

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
      lastHistoryIds = ""; // Force re-render
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

// Init
initWaveform();
loadHistory();
