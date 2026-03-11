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
const waveformSvg = document.getElementById("waveform-svg");
const waveformGlow = document.querySelector(".waveform-glow");
const progressStatus = document.getElementById("progress-status");
const activeResultEl = document.getElementById("active-result");
const errorEl = document.getElementById("error");
const historyList = document.getElementById("history-list");
const toastEl = document.getElementById("toast");
const cleanupBtn = document.getElementById("cleanup-btn");
const diarizeToggle = document.getElementById("diarize-toggle");
const durationToggle = document.getElementById("duration-toggle");

let abortController = null;
let pollTimer = null;
let lastHistoryIds = "";
let activeRecordId = null;
let etaInterval = null;
let etaRemaining = 0;
let etaBaseMessage = "";
const bodyCache = new Map();
const summaryCache = new Map();

// ─── SVG icons ───

const ICONS = {
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  sparkle: '🦄',
  obsidian: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 18v-6"/><path d="M14 18v-3"/></svg>',
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

marked.setOptions({ breaks: true });
function renderMarkdown(text) {
  return marked.parse(text);
}

const speakerColors = ["#f8bbd0", "#64b5f6", "#c6d84a", "#ffb74d", "#ce93d8", "#4dd0e1", "#ff8a65", "#aed581"];
function renderTranscript(text) {
  return escapeHtml(text).replace(
    /^([A-Z]):(\s)/gm,
    (_, letter, sp) => {
      const idx = letter.charCodeAt(0) - 65;
      const color = speakerColors[idx % speakerColors.length];
      return `<span class="speaker-label" style="color:${color}">${letter}:</span>${sp}`;
    }
  );
}

function formatObsidianDate(createdAt) {
  if (!createdAt) return new Date().toISOString().slice(0, 10);
  return new Date(createdAt + "Z").toISOString().slice(0, 10);
}

function escapeYamlString(str) {
  if (!str) return '""';
  if (/[:#"'|>\[\]{},%&!@`]/.test(str) || str.trim() !== str) {
    return '"' + str.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  }
  return str;
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatMinutes(seconds) {
  if (!seconds) return "0m";
  const m = Math.ceil(seconds / 60);
  return `${m}m`;
}

function formatEta(seconds) {
  if (!seconds || seconds <= 0) return "";
  if (seconds < 60) return `~ ${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s > 0 ? `~ ${m}m ${s}s` : `~ ${m}m`;
}

function startEtaCountdown(seconds, baseMessage) {
  stopEtaCountdown();
  etaRemaining = seconds;
  etaBaseMessage = baseMessage;
  etaInterval = setInterval(() => {
    etaRemaining--;
    if (etaRemaining <= 0) {
      progressStatus.textContent = etaBaseMessage;
      stopEtaCountdown();
      return;
    }
    progressStatus.textContent = etaBaseMessage + " " + formatEta(etaRemaining);
  }, 1000);
}

function stopEtaCountdown() {
  if (etaInterval) {
    clearInterval(etaInterval);
    etaInterval = null;
  }
  etaRemaining = 0;
  etaBaseMessage = "";
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
  transcribeBtn.disabled = isActive;
  cancelBtn.hidden = !isActive;
  urlInput.disabled = isActive;
  diarizeToggle.disabled = isActive;
  durationToggle.disabled = isActive;

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
  stopEtaCountdown();
  stopWaveAnimation();
  activeResultEl.innerHTML = "";
  activeRecordId = null;
  errorEl.hidden = true;
  errorEl.textContent = "";
}

// ─── Liquid Wave Engine ───

const WAVE_POINTS = 80;
const SVG_W = 640;
const SVG_H = 128;
const CENTER_Y = SVG_H / 2;
const LERP_AMP = 0.06;
const LERP_SPEED_RATE = 0.03;
const LERP_COLOR = 0.035;
const LERP_JAGGED = 0.02;
const REFLECTION_AMP = 0.6;
const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const WAVE_STATES = {
  stopped:      { amp: [4, 3, 2],       freq: [2.5, 3.0, 2.0], speed: [0.008, 0.006, 0.01],     color: [128,128,128], glow: 0 },
  downloading:  { amp: [38, 28, 18],    freq: [2.5, 3.0, 2.0], speed: [0.015, 0.012, 0.018],   color: [232,165,66],  glow: 0.4 },
  processing:   { amp: [30, 22, 14],    freq: [2.5, 3.0, 2.0], speed: [-0.04, -0.032, -0.045], color: [108,142,239], glow: 0.4 },
  transcribing: { amp: [34, 25, 16],    freq: [2.5, 3.0, 2.0], speed: [-0.04, -0.032, -0.045], color: [108,142,239], glow: 0.45 },
  done:         { amp: [5, 3.5, 2],     freq: [2.5, 3.0, 2.0], speed: [0.008, 0.006, 0.01],    color: [74,222,128],  glow: 0.5 },
  error:        { amp: [2, 1, 0.5],     freq: [8, 10, 6],      speed: [0.06, 0.05, 0.07],      color: [248,113,113], glow: 0.3 },
};

// Current interpolated wave params
const BASE_FREQ = [2.5, 3.0, 2.0];
let waveParams = {
  amp: [2, 1.5, 1],
  freq: [...BASE_FREQ],
  speed: [0.008, 0.006, 0.01],
  color: [128, 128, 128],
  glow: 0,
  phase: [0, Math.PI * 0.7, Math.PI * 1.4],
};
let waveTarget = { ...WAVE_STATES.stopped };
let waveRafId = null;
let waveRunning = false;
let jaggedMix = 0;
let jaggedTarget = 0;
let lastFrameTime = 0;
let isIdleThrottle = true;

// SVG path element refs
const wavePaths = {
  secondaryUpper: document.getElementById("wave-secondary-upper"),
  secondaryLower: document.getElementById("wave-secondary-lower"),
  primaryUpper:   document.getElementById("wave-primary-upper"),
  primaryLower:   document.getElementById("wave-primary-lower"),
  tertiaryUpper:  document.getElementById("wave-tertiary-upper"),
  tertiaryLower:  document.getElementById("wave-tertiary-lower"),
};

function lerp(a, b, t) { return a + (b - a) * t; }

function initWaveform() {
  waveParams = {
    amp: [2, 1.5, 1],
    freq: [...BASE_FREQ],
    speed: [0.008, 0.006, 0.01],
    color: [128, 128, 128],
    glow: 0,
    phase: [0, Math.PI * 0.7, Math.PI * 1.4],
  };
  waveTarget = { ...WAVE_STATES.stopped };
  jaggedMix = 0;
  jaggedTarget = 0;
  isIdleThrottle = true;
  updateGlow(0, [128, 128, 128]);
  if (!waveRunning) {
    waveRunning = true;
    lastFrameTime = performance.now();
    waveRafId = requestAnimationFrame(animateWave);
  }
}

function updateWaveform(state) {
  const stateKey =
    state === AppState.DOWNLOADING  ? "downloading" :
    state === AppState.PROCESSING   ? "processing" :
    state === AppState.TRANSCRIBING ? "transcribing" :
    state === AppState.DONE         ? "done" :
    state === AppState.ERROR        ? "error" : "stopped";

  isIdleThrottle = stateKey === "stopped" || stateKey === "done";

  if (stateKey === "done") {
    // Done bloom: spike amplitude, then let lerp settle
    waveTarget = { ...WAVE_STATES.done };
    waveParams.amp = [55, 42, 28];
    jaggedMix = 0;
    jaggedTarget = 0;
    // Brightness flash
    setTimeout(() => {
      waveformSvg.classList.add("flash");
      setTimeout(() => {
        waveformSvg.classList.remove("flash");
        waveformSvg.classList.add("flash-decay");
        setTimeout(() => waveformSvg.classList.remove("flash-decay"), 600);
      }, 150);
    }, 330);
  } else if (stateKey === "error") {
    // Error burst: spike amplitude + jagged, then decay
    waveTarget = { ...WAVE_STATES.error };
    waveParams.freq = [...waveTarget.freq];
    waveParams.amp = [40, 28, 18];
    jaggedMix = 1;
    jaggedTarget = 0;
  } else {
    waveTarget = { ...WAVE_STATES[stateKey] };
    jaggedTarget = 0;
  }

  updateGlow(waveTarget.glow, waveTarget.color);
}

function updateGlow(opacity, color) {
  if (!waveformGlow) return;
  if (opacity > 0) {
    waveformGlow.style.background = `rgba(${color[0]},${color[1]},${color[2]},0.3)`;
    waveformGlow.style.opacity = opacity;
  } else {
    waveformGlow.style.opacity = 0;
  }
}

function updateGradientColors(color) {
  const upperStops = document.querySelectorAll("#wave-grad-upper stop");
  const lowerStops = document.querySelectorAll("#wave-grad-lower stop");
  const rgb = `rgb(${color[0]},${color[1]},${color[2]})`;
  upperStops.forEach(s => s.setAttribute("stop-color", rgb));
  lowerStops.forEach(s => s.setAttribute("stop-color", rgb));
}

function animateWave(now) {
  if (!waveRunning) return;

  // Throttle to ~20fps when idle
  if (isIdleThrottle && now - lastFrameTime < 50) {
    waveRafId = requestAnimationFrame(animateWave);
    return;
  }
  lastFrameTime = now;

  // Reduced motion: render one static frame
  if (prefersReducedMotion.matches) {
    renderStaticWave();
    return;
  }

  // Lerp params toward targets
  for (let i = 0; i < 3; i++) {
    waveParams.amp[i]   = lerp(waveParams.amp[i],   waveTarget.amp[i],   LERP_AMP);
    waveParams.speed[i] = lerp(waveParams.speed[i], waveTarget.speed[i], LERP_SPEED_RATE);
    waveParams.phase[i] += waveParams.speed[i];
    waveParams.color[i] = lerp(waveParams.color[i], waveTarget.color[i], LERP_COLOR);
  }
  waveParams.glow = lerp(waveParams.glow, waveTarget.glow, LERP_COLOR);
  jaggedMix = lerp(jaggedMix, jaggedTarget, LERP_JAGGED);

  updateGradientColors(waveParams.color);

  // Generate paths for 3 layers
  const layers = [
    { upper: wavePaths.primaryUpper,   lower: wavePaths.primaryLower,   idx: 0 },
    { upper: wavePaths.secondaryUpper, lower: wavePaths.secondaryLower, idx: 1 },
    { upper: wavePaths.tertiaryUpper,  lower: wavePaths.tertiaryLower,  idx: 2 },
  ];

  for (const layer of layers) {
    const amp = waveParams.amp[layer.idx];
    const freq = waveParams.freq[layer.idx];
    const phase = waveParams.phase[layer.idx];

    const upperPoints = generateWavePoints(amp, freq, phase);
    const lowerPoints = upperPoints.map(p => ({
      x: p.x,
      y: CENTER_Y + (CENTER_Y - p.y) * REFLECTION_AMP,
    }));

    layer.upper.setAttribute("d", buildSmoothPath(upperPoints));
    layer.lower.setAttribute("d", buildSmoothPath(lowerPoints));
  }

  waveRafId = requestAnimationFrame(animateWave);
}

function generateWavePoints(amp, freq, phase) {
  const points = [];
  for (let i = 0; i < WAVE_POINTS; i++) {
    const t = i / (WAVE_POINTS - 1);
    const x = t * SVG_W;

    // Edge tapering window — exponent concentrates energy in center
    const window = Math.pow(Math.sin(Math.PI * t), 3.0);

    // Primary sine + harmonics
    let y = Math.sin(t * Math.PI * 2 * freq + phase) * amp;
    y += Math.sin(t * Math.PI * 2 * freq * 2.1 + phase * 1.3) * amp * 0.15;
    y += Math.sin(t * Math.PI * 2 * freq * 0.5 + phase * 0.7) * amp * 0.1;

    // Jagged noise for error state
    if (jaggedMix > 0.01) {
      const noise = Math.sin(t * 47 + phase * 3) * amp * 0.6
                   + Math.sin(t * 23 + phase * 7) * amp * 0.3;
      y += noise * jaggedMix;
    }

    y *= window;
    points.push({ x, y: CENTER_Y - y });
  }
  return points;
}

function buildSmoothPath(points) {
  if (points.length < 2) return "";

  // Catmull-Rom to cubic bezier conversion
  let d = `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[Math.max(0, i - 1)];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[Math.min(points.length - 1, i + 2)];

    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    d += ` C${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
  }

  // Close path to center line for gradient fill
  d += ` L${SVG_W},${CENTER_Y} L0,${CENTER_Y} Z`;

  return d;
}

function renderStaticWave() {
  // Single static frame for reduced-motion
  updateGradientColors(waveTarget.color);
  const layers = [
    { upper: wavePaths.primaryUpper,   lower: wavePaths.primaryLower,   idx: 0 },
    { upper: wavePaths.secondaryUpper, lower: wavePaths.secondaryLower, idx: 1 },
    { upper: wavePaths.tertiaryUpper,  lower: wavePaths.tertiaryLower,  idx: 2 },
  ];
  for (const layer of layers) {
    const amp = waveTarget.amp[layer.idx] * 0.5;
    const points = generateWavePoints(amp, waveTarget.freq[layer.idx], layer.idx * 1.2);
    const lowerPts = points.map(p => ({ x: p.x, y: CENTER_Y + (CENTER_Y - p.y) * REFLECTION_AMP }));
    layer.upper.setAttribute("d", buildSmoothPath(points));
    layer.lower.setAttribute("d", buildSmoothPath(lowerPts));
  }
}

function stopWaveAnimation() {
  waveRunning = false;
  if (waveRafId) {
    cancelAnimationFrame(waveRafId);
    waveRafId = null;
  }
  // Clear all paths
  Object.values(wavePaths).forEach(p => { if (p) p.setAttribute("d", ""); });
  updateGlow(0, [128, 128, 128]);
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
  const dur = record.duration_limit && record.duration_limit < record.duration
    ? `${formatMinutes(record.duration_limit)} of ${formatDuration(record.duration)}`
    : formatDuration(record.duration);
  const statusLabel = record.status === "in_progress" ? "in progress" : record.status;
  const modelLabel = record.model && record.model.includes("-diarize") ? "speakers" : "";
  const partialLabel = record.duration_limit && record.duration_limit > 0 ? `first ${formatMinutes(record.duration_limit)}` : "";
  const wordsLabel = record.words ? `${record.words} words` : "";
  const meta = [statusLabel, modelLabel, partialLabel, dur, wordsLabel, date].filter(Boolean).join(" \u00b7 ");

  let actions = "";
  let quickCopy = "";
  let quickSummarize = "";
  if (record.status === "done") {
    const summarizeTitle = record.has_summary ? "Re-summarize" : "Summarize";
    quickSummarize = `<button class="quick-summarize${record.has_summary ? " has-summary" : ""}" onclick="event.stopPropagation(); openSummarizePrompt('${record.id}')" title="${summarizeTitle}"><span class="unicorn-icon">${ICONS.sparkle}</span> Magic</button>`;
    quickCopy = `<button class="quick-copy" onclick="event.stopPropagation(); copyRecordText('${record.id}')" title="Copy">${ICONS.copy} Copy</button>`;
    actions = `
      <button onclick="event.stopPropagation(); exportToObsidian('${record.id}')" title="Send to Obsidian">${ICONS.obsidian} Obsidian</button>
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
    bodyHtml = `<div class="card-body" onclick="event.stopPropagation()">${renderTranscript(opts.bodyText)}</div>`;
  }

  const hasSummary = record.has_summary ? "true" : "false";

  return `
    <div class="history-card ${expandable} ${expandedClass}" data-id="${record.id}" data-status="${record.status}" data-has-summary="${hasSummary}" onclick="handleCardClick(this)">
      <div class="card-content">
        <div class="card-title">${escapeHtml(record.title)}</div>
        <div class="card-meta"><span class="status-dot ${record.status}"></span>${meta}${record.status === "error" && record.error ? " \u00b7 " + escapeHtml(record.error) : ""}</div>
        ${actions ? `<div class="card-actions">${actions}</div>` : ""}
      </div>
      ${quickCopy}${quickSummarize}
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
    duration_limit: data.duration_limit || 0,
    model: data.model || "",
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
  const card = document.querySelector(`.history-card[data-id="${id}"]`);
  const activeTab = card?.querySelector(".card-tab.active");
  const isSummary = activeTab?.dataset.tab === "summary";

  if (isSummary) {
    const data = await getRecordSummary(id);
    if (!data?.summary) return;
    await navigator.clipboard.writeText(data.summary);
    showToast("Summary copied");
  } else {
    const text = await getRecordBody(id);
    if (!text) return;
    await navigator.clipboard.writeText(text);
    showToast("Transcript copied");
  }
}

async function downloadRecordText(id, title) {
  const card = document.querySelector(`.history-card[data-id="${id}"]`);
  const activeTab = card?.querySelector(".card-tab.active");
  const isSummary = activeTab?.dataset.tab === "summary";

  let text;
  let suffix;
  if (isSummary) {
    const data = await getRecordSummary(id);
    if (!data?.summary) return;
    text = data.summary;
    suffix = "_summary";
  } else {
    text = await getRecordBody(id);
    if (!text) return;
    suffix = "";
  }
  const blob = new Blob([text], { type: "text/plain" });
  const a = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  a.href = objectUrl;
  const filename = (title || "transcript").replace(/[^a-zA-Z0-9_\- ]/g, "").trim() || "transcript";
  a.download = `${filename}${suffix}.txt`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 5000);
}

async function buildObsidianMarkdown(id) {
  const card = document.querySelector(`.history-card[data-id="${id}"]`);
  const activeTab = card?.querySelector(".card-tab.active");
  const isSummary = activeTab?.dataset.tab === "summary";

  const record = await getFullRecord(id);
  if (!record) return null;

  const lines = ["---"];
  lines.push(`title: ${escapeYamlString(record.title || "")}`);
  lines.push(`url: ${record.url || ""}`);
  lines.push(`date: ${formatObsidianDate(record.created_at)}`);

  let body;
  if (isSummary) {
    const data = await getRecordSummary(id);
    if (!data?.summary) return null;
    body = data.summary;
    lines.push("type: summary");
    lines.push(`model: ${record.model || ""}`);
    if (record.duration) lines.push(`duration: ${formatDuration(record.duration)}`);
    if (record.words) lines.push(`words: ${record.words}`);
    lines.push(`prompt: ${escapeYamlString(data.prompt || "")}`);
  } else {
    body = record.body || await getRecordBody(id);
    if (!body) return null;
    lines.push("type: transcript");
    lines.push(`model: ${record.model || ""}`);
    if (record.duration) lines.push(`duration: ${formatDuration(record.duration)}`);
    if (record.words) lines.push(`words: ${record.words}`);
  }

  lines.push("tags:");
  lines.push("  - transcript-maker");
  lines.push("---");
  lines.push("");
  lines.push(body);

  return { markdown: lines.join("\n"), title: record.title, isSummary };
}

async function exportToObsidian(id) {
  const vault = localStorage.getItem("tm_obsidian_vault");
  if (!vault) {
    showObsidianConfig(id);
    return;
  }

  const result = await buildObsidianMarkdown(id);
  if (!result) return;

  await navigator.clipboard.writeText(result.markdown);

  const subfolder = localStorage.getItem("tm_obsidian_subfolder") || "";
  const suffix = result.isSummary ? " (Summary)" : " (Transcript)";
  const filename = ((result.title || "Untitled") + suffix).replace(/[\\/:*?"<>|]/g, "-").trim();
  const filePath = subfolder ? `${subfolder}/${filename}` : filename;

  const uri = `obsidian://new?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(filePath)}&clipboard&overwrite`;
  window.location.href = uri;

  showToast("Sent to Obsidian");
}

function showObsidianConfig(id) {
  // If already open, close it
  const existing = document.querySelector(".obsidian-modal");
  if (existing) {
    existing.remove();
    return;
  }

  const savedVault = localStorage.getItem("tm_obsidian_vault") || "";
  const savedSubfolder = localStorage.getItem("tm_obsidian_subfolder") || "";

  const modal = document.createElement("div");
  modal.className = "obsidian-modal";
  modal.innerHTML = `
    <div class="obsidian-modal-backdrop"></div>
    <div class="obsidian-modal-content">
      <div class="obsidian-config-hint">Enter your Obsidian vault name and an optional folder path. This is a one-time setup — your choice will be remembered for all future exports.</div>
      <div class="obsidian-path-bar">
        <input type="text" class="obsidian-vault-input" placeholder="Vault name" value="${escapeHtml(savedVault)}" />
        <span class="obsidian-path-sep">/</span>
        <input type="text" class="obsidian-subfolder-input" placeholder="folder/path (optional)" value="${escapeHtml(savedSubfolder)}" />
        <button class="obsidian-connect-btn" onclick="saveObsidianConfig('${id}')">Connect</button>
        <button class="obsidian-cancel-btn" onclick="document.querySelector('.obsidian-modal').remove()">Cancel</button>
      </div>
    </div>
  `;

  modal.querySelector(".obsidian-modal-backdrop").addEventListener("click", () => modal.remove());

  document.body.appendChild(modal);
  modal.querySelector(".obsidian-vault-input").focus();
}

function saveObsidianConfig(id) {
  const modal = document.querySelector(".obsidian-modal");
  if (!modal) return;

  const vaultInput = modal.querySelector(".obsidian-vault-input");
  const subfolderInput = modal.querySelector(".obsidian-subfolder-input");
  const vault = vaultInput?.value.trim();
  const subfolder = subfolderInput?.value.trim();

  if (!vault) {
    vaultInput.focus();
    return;
  }

  localStorage.setItem("tm_obsidian_vault", vault);
  if (subfolder) {
    localStorage.setItem("tm_obsidian_subfolder", subfolder);
  } else {
    localStorage.removeItem("tm_obsidian_subfolder");
  }

  modal.remove();
  exportToObsidian(id);
}

function clearObsidianConfig() {
  localStorage.removeItem("tm_obsidian_vault");
  localStorage.removeItem("tm_obsidian_subfolder");
  showToast("Obsidian vault config cleared");
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

async function getFullRecord(id) {
  const res = await fetch(`/api/history/${id}`);
  if (!res.ok) return null;
  return await res.json();
}

// ─── Transcription ───

async function runSSE(fetchUrl, fetchBody) {
  resetUI();
  lastHistoryIds = "";
  loadHistory();
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
    let currentEvent = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (line.startsWith("event:")) {
          currentEvent = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          const raw = line.slice(5).trim();
          if (!raw) continue;
          const data = JSON.parse(raw);
          handleEvent(currentEvent, data);
          currentEvent = null;
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
    transcribeBtn.disabled = false;
    cancelBtn.hidden = true;
    urlInput.disabled = false;
    diarizeToggle.disabled = false;
    durationToggle.disabled = false;
    loadHistory();
  }
}

const DEMO_MODE = new URLSearchParams(window.location.search).has("demo");
const apiPrefix = DEMO_MODE ? "/api/demo" : "/api";

function getDurationLimit() {
  const toggle = document.getElementById("duration-toggle");
  if (!toggle || !toggle.checked) return 0;
  const val = parseInt(document.getElementById("duration-limit-input").value, 10);
  return val > 0 ? val : 0;
}

async function startTranscription(url) {
  const diarize = document.getElementById("diarize-toggle").checked;
  const duration_limit = getDurationLimit();
  const model = getTranscribeModel();
  runSSE(`${apiPrefix}/transcribe`, { url, diarize, duration_limit, model });
}

function retranscribe(id) {
  const diarize = document.getElementById("diarize-toggle").checked;
  const duration_limit = getDurationLimit();
  const model = getTranscribeModel();
  runSSE(`${apiPrefix}/history/${id}/retranscribe`, { diarize, duration_limit, model });
}

function handleEvent(event, data) {
  switch (event) {
    case "progress":
      if (data.stage === "downloading") {
        setState(AppState.DOWNLOADING, { message: data.message });
      } else if (data.stage === "processing") {
        setState(AppState.PROCESSING, { message: data.message });
      } else if (data.stage === "transcribing") {
        if (data.eta_seconds) {
          setState(AppState.TRANSCRIBING, { message: data.message + " " + formatEta(data.eta_seconds) });
          startEtaCountdown(data.eta_seconds, data.message);
        } else {
          stopEtaCountdown();
          setState(AppState.TRANSCRIBING, { message: data.message });
        }
      }
      break;

    case "transcript":
      activeRecordId = data.record_id || null;
      setState(AppState.DONE, {
        text: data.text,
        title: data.title || "Transcript",
        duration_seconds: data.duration_seconds,
        duration_limit: data.duration_limit || 0,
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
  const busy = [AppState.DOWNLOADING, AppState.PROCESSING, AppState.TRANSCRIBING].includes(currentState);
  if (busy) return;
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
    transcribeBtn.disabled = false;
    cancelBtn.hidden = true;
    urlInput.disabled = false;
    diarizeToggle.disabled = false;
    durationToggle.disabled = false;
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") transcribeBtn.click();
});

document.getElementById("duration-toggle").addEventListener("change", (e) => {
  urlInput.blur();
  const group = document.querySelector(".duration-input-group");
  if (e.target.checked) {
    group.classList.add("visible");
  } else {
    group.classList.remove("visible");
  }
});

document.getElementById("diarize-toggle").addEventListener("change", () => {
  urlInput.blur();
});

document.getElementById("duration-limit-input").addEventListener("focus", () => {
  urlInput.blur();
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
      summaryCache.clear();
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
  const existing = cardEl.querySelector(".card-body, .card-tabs, .card-summary, .summarize-prompt");
  const top = cardEl.getBoundingClientRect().top;
  if (existing) {
    // Collapse — remove all expanded elements
    cardEl.querySelectorAll(".card-body, .card-tabs, .card-summary, .summarize-prompt").forEach(el => el.remove());
    cardEl.classList.remove("expanded");
  } else {
    const body = await getRecordBody(id);
    if (!body) return;

    const hasSummary = cardEl.dataset.hasSummary === "true";
    let summaryData = null;
    if (hasSummary) {
      summaryData = await getRecordSummary(id);
    }

    // Build tab bar + content
    if (summaryData) {
      const tabBar = buildTabBar(id, "summary");
      tabBar.addEventListener("click", (e) => e.stopPropagation());
      cardEl.appendChild(tabBar);

      const bodyDiv = document.createElement("div");
      bodyDiv.className = "card-body";
      bodyDiv.style.display = "none";
      bodyDiv.innerHTML = renderTranscript(body);
      bodyDiv.addEventListener("click", (e) => e.stopPropagation());
      cardEl.appendChild(bodyDiv);

      const summaryDiv = buildSummaryDiv(summaryData.summary);
      cardEl.appendChild(summaryDiv);
    } else {
      const div = document.createElement("div");
      div.className = "card-body";
      div.innerHTML = renderTranscript(body);
      div.addEventListener("click", (e) => e.stopPropagation());
      cardEl.appendChild(div);
    }

    cardEl.classList.add("expanded");
  }
  const shift = cardEl.getBoundingClientRect().top - top;
  if (shift) window.scrollBy(0, shift);
}

function buildTabBar(id, activeTab) {
  const bar = document.createElement("div");
  bar.className = "card-tabs";
  bar.innerHTML = `
    <button class="card-tab ${activeTab === "transcript" ? "active" : ""}" data-tab="transcript">Transcript</button>
    <button class="card-tab ${activeTab === "summary" ? "active" : ""}" data-tab="summary">Summary</button>
  `;
  bar.addEventListener("click", (e) => {
    const tab = e.target.closest(".card-tab");
    if (!tab) return;
    const card = bar.closest(".history-card");
    const tabName = tab.dataset.tab;
    // Toggle active state
    bar.querySelectorAll(".card-tab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    // Show/hide content
    const bodyEl = card.querySelector(".card-body");
    const summaryEl = card.querySelector(".card-summary");
    if (bodyEl) bodyEl.style.display = tabName === "transcript" ? "" : "none";
    if (summaryEl) summaryEl.style.display = tabName === "summary" ? "" : "none";
  });
  return bar;
}

function buildSummaryDiv(summaryText) {
  const div = document.createElement("div");
  div.className = "card-summary";
  div.addEventListener("click", (e) => e.stopPropagation());
  div.innerHTML = `<div class="card-summary-text">${renderMarkdown(summaryText)}</div>`;
  return div;
}

async function getRecordSummary(id) {
  if (summaryCache.has(id)) return summaryCache.get(id);
  const res = await fetch(`/api/history/${id}/summary`);
  if (!res.ok) return null;
  const data = await res.json();
  summaryCache.set(id, data);
  return data;
}

function openSummarizePrompt(id) {
  const card = document.querySelector(`.history-card[data-id="${id}"]`);
  if (!card) return;

  // If prompt already open, close it
  const existing = card.querySelector(".summarize-prompt");
  if (existing) {
    existing.remove();
    return;
  }

  // Ensure card is expanded
  if (!card.classList.contains("expanded")) {
    handleCardClick(card).then(() => addPromptUI(id, card));
    return;
  }
  addPromptUI(id, card);
}

function addPromptUI(id, card) {
  // Remove any existing prompt
  const existing = card.querySelector(".summarize-prompt");
  if (existing) existing.remove();

  const prompt = document.createElement("div");
  prompt.className = "summarize-prompt";
  prompt.addEventListener("click", (e) => e.stopPropagation());
  prompt.innerHTML = `
    <div class="summarize-bar">
      <textarea class="summarize-input" rows="2" placeholder="Summarize this transcript concisely, highlighting key points and main topics discussed.">${localStorage.getItem("tm_summarize_prompt") || ""}</textarea>
      <div class="summarize-bar-actions">
        <button class="summarize-generate-btn" onclick="generateSummary('${id}')">
          <span>Generate</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
        </button>
        <button class="summarize-cancel-btn" onclick="this.closest('.summarize-prompt').remove()">Cancel</button>
      </div>
    </div>
  `;

  // Insert after card-actions or at end of card-content
  const actions = card.querySelector(".card-actions");
  if (actions) {
    actions.after(prompt);
  } else {
    const content = card.querySelector(".card-content");
    content.after(prompt);
  }

  // Auto-grow textarea to fit content
  const textarea = prompt.querySelector(".summarize-input");
  const autoGrow = () => {
    textarea.style.height = "auto";
    textarea.style.height = textarea.scrollHeight + "px";
  };
  textarea.addEventListener("input", autoGrow);
  autoGrow();
}

async function generateSummary(id) {
  const card = document.querySelector(`.history-card[data-id="${id}"]`);
  if (!card) return;

  const textarea = card.querySelector(".summarize-input");
  const btn = card.querySelector(".summarize-generate-btn");
  const promptText = textarea ? textarea.value.trim() : "";

  btn.disabled = true;
  btn.textContent = "Generating...";

  try {
    const res = await fetch(`${apiPrefix}/history/${id}/summarize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: promptText, model: getSummarizeModel() }),
    });

    if (!res.ok) {
      const data = await res.json();
      throw new Error(data.error || "Summarization failed");
    }

    const data = await res.json();
    localStorage.setItem("tm_summarize_prompt", promptText);
    summaryCache.set(id, data);
    card.dataset.hasSummary = "true";

    // Remove prompt UI
    const promptEl = card.querySelector(".summarize-prompt");
    if (promptEl) promptEl.remove();

    // Mark the quick-summarize button as having a summary
    const sumBtn = card.querySelector(".quick-summarize");
    if (sumBtn) sumBtn.classList.add("has-summary");

    // Rebuild tab bar and summary display
    const oldTabs = card.querySelector(".card-tabs");
    const oldSummary = card.querySelector(".card-summary");
    const oldBody = card.querySelector(".card-body");

    if (oldTabs) oldTabs.remove();
    if (oldSummary) oldSummary.remove();

    // Add tabs if not present
    const tabBar = buildTabBar(id, "summary");
    tabBar.addEventListener("click", (e) => e.stopPropagation());
    if (oldBody) {
      oldBody.before(tabBar);
      oldBody.style.display = "none";
    } else {
      card.appendChild(tabBar);
    }

    const summaryDiv = buildSummaryDiv(data.summary);
    if (oldBody) {
      oldBody.after(summaryDiv);
    } else {
      card.appendChild(summaryDiv);
    }

    showToast("Summary generated");
  } catch (err) {
    showToast(err.message);
    btn.disabled = false;
    btn.textContent = "Generate";
  }
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
      clearObsidianConfig();
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

// ─── Provider selector ───

let providers = [];
let selectedProvider = null;

const PROVIDER_COLORS = {
  openai: "#4ade80",
  gemini: "#60a5fa",
};

async function loadProviders() {
  try {
    const res = await fetch("/api/providers");
    if (!res.ok) return;
    const data = await res.json();
    providers = data.providers || [];

    if (providers.length < 2) return;

    const stored = localStorage.getItem("tm_provider");
    selectedProvider = providers.find(p => p.id === stored) || providers[0];

    const el = document.getElementById("provider-selector");
    el.hidden = false;
    updateProviderUI();
  } catch {
    // Silently ignore
  }
}

function updateProviderUI() {
  const nameEl = document.querySelector(".provider-name");
  const dotEl = document.querySelector(".provider-dot");
  if (!selectedProvider || !nameEl) return;
  nameEl.textContent = selectedProvider.label;
  dotEl.style.background = PROVIDER_COLORS[selectedProvider.id] || PROVIDER_COLORS.openai;
}

function toggleProvider() {
  if (providers.length < 2) return;
  const idx = providers.findIndex(p => p.id === selectedProvider.id);
  selectedProvider = providers[(idx + 1) % providers.length];
  localStorage.setItem("tm_provider", selectedProvider.id);
  updateProviderUI();
}

function getTranscribeModel() {
  return selectedProvider?.transcribe_model || "";
}

function getSummarizeModel() {
  return selectedProvider?.summarize_model || "";
}

// ─── Init ───

if (DEMO_MODE) {
  const badge = document.createElement("div");
  badge.className = "demo-badge";
  badge.textContent = "Demo Mode";
  document.body.appendChild(badge);
}

loadHistory();
loadProviders();
