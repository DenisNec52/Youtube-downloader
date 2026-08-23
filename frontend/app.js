const els = {
  url: document.getElementById("url"),
  analyzeBtn: document.getElementById("analyze-btn"),
  result: document.getElementById("result"),
  thumb: document.getElementById("thumb"),
  title: document.getElementById("video-title"),
  meta: document.getElementById("video-meta"),
  quality: document.getElementById("quality"),
  outdirRow: document.getElementById("outdir-row"),
  outdir: document.getElementById("outdir"),
  downloadBtn: document.getElementById("download-btn"),
  error: document.getElementById("error"),
  ffmpegWarning: document.getElementById("ffmpeg-warning"),
  modeBtns: document.querySelectorAll(".mode-btn"),
  playlistRow: document.getElementById("playlist-row"),
  wholePlaylist: document.getElementById("whole-playlist"),
  subsRow: document.getElementById("subs-row"),
  embedSubs: document.getElementById("embed-subs"),
  queueSection: document.getElementById("queue-section"),
  queueList: document.getElementById("queue-list"),
  historyList: document.getElementById("history-list"),
  refreshHistoryBtn: document.getElementById("refresh-history-btn"),
  ytdlpVersion: document.getElementById("ytdlp-version"),
  updateBtn: document.getElementById("update-btn"),
  apikeyRow: document.getElementById("apikey-row"),
  apikey: document.getElementById("apikey"),
  cookiesRow: document.getElementById("cookies-row"),
  cookieBrowser: document.getElementById("cookie-browser"),
  googleCard: document.getElementById("google-card"),
  googleActions: document.getElementById("google-actions"),
  googleStatus: document.getElementById("google-status"),
  googlePlaylists: document.getElementById("google-playlists"),
};

let state = {
  mode: "video",
  videoQualities: [],
  audioQualities: [],
  hasSubtitles: false,
  serverMode: "local", // "local" | "web", da /api/status
};

const activeJobs = new Map(); // job_id -> { interval, cardEl }

// API base configurabile per il deploy separato frontend (Vercel) / backend
// (Render): se frontend/config.js definisce window.YTGRABBER_API_BASE, lo
// usiamo come prefisso di tutte le chiamate; in locale resta vuoto (stessa origine).
const API_BASE = window.YTGRABBER_API_BASE || "";

els.apikey.value = localStorage.getItem("ytgrabber_api_key") || "";
els.apikey.addEventListener("change", () => {
  localStorage.setItem("ytgrabber_api_key", els.apikey.value.trim());
});

function authHeaders(extra = {}) {
  const key = els.apikey.value.trim();
  return key ? { ...extra, "X-API-Key": key } : extra;
}

function api(path, opts = {}) {
  return fetch(API_BASE + path, opts);
}

function showError(msg) {
  els.error.textContent = msg;
  els.error.classList.remove("hidden");
}

function clearError() {
  els.error.classList.add("hidden");
}

function fmtDuration(sec) {
  sec = Math.round(sec || 0);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function fmtSpeed(bytesPerSec) {
  if (!bytesPerSec) return "";
  const mb = bytesPerSec / 1024 / 1024;
  return mb >= 1 ? `${mb.toFixed(1)} MB/s` : `${(bytesPerSec / 1024).toFixed(0)} KB/s`;
}

function populateQualitySelect() {
  els.quality.innerHTML = "";
  const list = state.mode === "video" ? state.videoQualities : state.audioQualities;
  for (const q of list) {
    const opt = document.createElement("option");
    opt.value = q;
    opt.textContent = state.mode === "video" ? `${q}p` : `${q} kbps (MP3)`;
    els.quality.appendChild(opt);
  }
  els.subsRow.classList.toggle("hidden", !(state.mode === "video" && state.hasSubtitles));
}

els.modeBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    els.modeBtns.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.mode = btn.dataset.mode;
    populateQualitySelect();
  });
});

async function loadStatus() {
  const res = await api("/api/status");
  const data = await res.json();
  state.serverMode = data.mode;

  els.outdir.value = data.default_output_dir;
  els.ytdlpVersion.textContent = `yt-dlp ${data.yt_dlp_version}${data.aria2c_available ? " · aria2c attivo" : ""}`;
  if (!data.ffmpeg_available) {
    els.ffmpegWarning.classList.remove("hidden");
  }

  const isWeb = data.mode === "web";
  els.outdirRow.classList.toggle("hidden", isWeb);
  els.apikeyRow.classList.toggle("hidden", !data.auth_required);

  if (data.cookie_browsers && data.cookie_browsers.length) {
    els.cookiesRow.classList.remove("hidden");
    for (const name of data.cookie_browsers) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name[0].toUpperCase() + name.slice(1);
      els.cookieBrowser.appendChild(opt);
    }
  }

  if (!isWeb) {
    els.googleCard.classList.remove("hidden");
    loadGoogleStatus();
  }
}

async function loadGoogleStatus() {
  const res = await api("/api/google/status");
  const data = await res.json();

  if (!data.configured) {
    els.googleStatus.textContent =
      "Non configurato: aggiungi google_client_secret.json nella cartella dell'app (vedi README) per abilitare il login.";
    els.googleActions.innerHTML = "";
    return;
  }

  if (data.connecting) {
    els.googleStatus.textContent = "Completa l'accesso nella finestra del browser appena aperta...";
    els.googleActions.innerHTML = "";
    setTimeout(loadGoogleStatus, 1500);
    return;
  }

  if (data.error) {
    showError("Login Google fallito: " + data.error);
  }

  if (data.connected) {
    els.googleStatus.textContent = "Collegato.";
    els.googleActions.innerHTML = `<button class="small" id="google-disconnect-btn">Scollega</button>`;
    document.getElementById("google-disconnect-btn").addEventListener("click", async () => {
      await api("/api/google/disconnect", { method: "POST" });
      els.googlePlaylists.innerHTML = "";
      loadGoogleStatus();
    });
    loadGooglePlaylists();
  } else {
    els.googleStatus.textContent = "Non collegato.";
    els.googleActions.innerHTML = `<button class="small primary-link" id="google-connect-btn">Accedi con Google</button>`;
    els.googlePlaylists.innerHTML = "";
    document.getElementById("google-connect-btn").addEventListener("click", async () => {
      await api("/api/google/connect", { method: "POST" });
      loadGoogleStatus();
    });
  }
}

async function loadGooglePlaylists() {
  const res = await api("/api/google/playlists");
  if (!res.ok) return;
  const playlists = await res.json();
  els.googlePlaylists.innerHTML = "";
  for (const pl of playlists) {
    const row = document.createElement("div");
    row.className = "history-item";
    row.innerHTML = `
      <span>${pl.title} <span class="muted">— ${pl.item_count} video</span></span>
      <button class="small">Analizza</button>
    `;
    row.querySelector("button").addEventListener("click", () => {
      els.url.value = pl.url;
      els.wholePlaylist.checked = true;
      els.analyzeBtn.click();
    });
    els.googlePlaylists.appendChild(row);
  }
}

els.updateBtn.addEventListener("click", async () => {
  els.updateBtn.disabled = true;
  els.updateBtn.textContent = "Aggiornamento...";
  try {
    const res = await api("/api/update-yt-dlp", { method: "POST", headers: authHeaders() });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Aggiornamento fallito");
    }
    const data = await res.json();
    els.ytdlpVersion.textContent = `yt-dlp ${data.new_version}`;
  } catch (e) {
    showError("Aggiornamento yt-dlp fallito: " + e.message);
  } finally {
    els.updateBtn.disabled = false;
    els.updateBtn.textContent = "Aggiorna yt-dlp";
  }
});

els.analyzeBtn.addEventListener("click", async () => {
  clearError();
  const url = els.url.value.trim();
  if (!url) return;

  els.analyzeBtn.disabled = true;
  els.analyzeBtn.textContent = "Analisi...";
  try {
    const res = await api("/api/probe", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ url, browser_cookies: els.cookieBrowser.value || null }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Errore durante l'analisi");
    }
    const info = await res.json();

    els.title.textContent = info.title;
    els.meta.textContent = info.is_playlist
      ? `Playlist — ${info.playlist_count} elementi`
      : fmtDuration(info.duration);
    els.thumb.src = info.thumbnail || "";
    state.videoQualities = info.video_qualities;
    state.audioQualities = info.audio_qualities;
    state.hasSubtitles = info.has_subtitles;
    populateQualitySelect();
    els.playlistRow.classList.toggle("hidden", !info.is_playlist);
    els.result.classList.remove("hidden");
  } catch (e) {
    showError(e.message);
  } finally {
    els.analyzeBtn.disabled = false;
    els.analyzeBtn.textContent = "Analizza";
  }
});

els.downloadBtn.addEventListener("click", async () => {
  clearError();
  const url = els.url.value.trim();
  const quality = parseInt(els.quality.value, 10);
  const output_dir = state.serverMode === "web" ? null : (els.outdir.value.trim() || null);
  const embed_subs = els.embedSubs.checked;
  const whole_playlist = els.wholePlaylist.checked;
  const titleGuess = els.title.textContent || url;

  try {
    const res = await api("/api/download", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        url, mode: state.mode, quality, output_dir, embed_subs, whole_playlist,
        browser_cookies: els.cookieBrowser.value || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Errore durante l'avvio del download");
    }
    const { job_id } = await res.json();
    addJobCard(job_id, titleGuess);
  } catch (e) {
    showError(e.message);
  }
});

function addJobCard(jobId, titleGuess) {
  els.queueSection.classList.remove("hidden");

  const card = document.createElement("div");
  card.className = "job-card";
  card.innerHTML = `
    <div class="job-title">${titleGuess}</div>
    <div class="job-progress-bar"><div class="job-progress-fill" style="width:0%"></div></div>
    <div class="job-actions">
      <span class="job-status">In coda...</span>
      <button class="small cancel-btn">Annulla</button>
    </div>
  `;
  els.queueList.prepend(card);

  const fill = card.querySelector(".job-progress-fill");
  const statusEl = card.querySelector(".job-status");
  const actionsEl = card.querySelector(".job-actions");
  const cancelBtn = card.querySelector(".cancel-btn");

  cancelBtn.addEventListener("click", async () => {
    await api(`/api/cancel/${jobId}`, { method: "POST", headers: authHeaders() });
    cancelBtn.disabled = true;
  });

  const interval = setInterval(async () => {
    const res = await api(`/api/progress/${jobId}`);
    if (!res.ok) {
      clearInterval(interval);
      return;
    }
    const job = await res.json();
    fill.style.width = `${job.percent || 0}%`;

    if (job.status === "downloading") {
      const playlistPrefix = job.playlist_progress ? `[${job.playlist_progress}] ` : "";
      statusEl.textContent = `${playlistPrefix}${job.filename || ""} — ${job.percent}% — ${fmtSpeed(job.speed)}`;
    } else if (job.status === "processing") {
      statusEl.textContent = "Conversione in corso...";
    } else if (job.status === "done") {
      statusEl.textContent = state.serverMode === "web" ? "Completato" : `Completato — ${job.output_dir}`;
      statusEl.classList.add("status-done");
      cancelBtn.remove();
      if (state.serverMode === "web") {
        const dlBtn = document.createElement("button");
        dlBtn.className = "small primary-link";
        dlBtn.textContent = "Scarica file";
        dlBtn.addEventListener("click", () => {
          window.open(API_BASE + `/api/file/${jobId}`, "_blank");
        });
        actionsEl.appendChild(dlBtn);
      }
      clearInterval(interval);
      loadHistory();
    } else if (job.status === "cancelled") {
      statusEl.textContent = "Annullato.";
      statusEl.classList.add("status-cancelled");
      cancelBtn.remove();
      clearInterval(interval);
    } else if (job.status === "error") {
      statusEl.textContent = job.error || "Errore sconosciuto";
      statusEl.classList.add("status-error");
      cancelBtn.remove();
      clearInterval(interval);
    }
  }, 700);

  activeJobs.set(jobId, { interval, card });
}

async function loadHistory() {
  const res = await api("/api/history");
  const items = await res.json();
  if (!items.length) {
    els.historyList.textContent = "Nessun download ancora.";
    return;
  }
  els.historyList.innerHTML = "";
  for (const item of items.slice(0, 30)) {
    const row = document.createElement("div");
    row.className = "history-item";
    const date = new Date(item.timestamp * 1000).toLocaleString("it-IT");
    const actionHtml = state.serverMode === "web"
      ? ""
      : `<button class="small open-folder-btn">Apri cartella</button>`;
    row.innerHTML = `<span>${item.title} <span class="muted">— ${date}</span></span>${actionHtml}`;
    const btn = row.querySelector(".open-folder-btn");
    if (btn) {
      btn.addEventListener("click", () => {
        api("/api/open-folder", {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ path: item.output_dir }),
        });
      });
    }
    els.historyList.appendChild(row);
  }
}

els.refreshHistoryBtn.addEventListener("click", loadHistory);

loadStatus();
loadHistory();
