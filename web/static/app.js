// Kaamelott GIF, web app: searches a cue through the API and shares it as a GIF.
// The page is served on the API's domain (under /app/), so it calls the API on the same origin:
// no CORS needed. The key is typed once and stays in this device's local storage.
"use strict";

const STORAGE_KEY = "kaamelott-gif.cle";
const MAX_RESULTS = 12;
const THUMB = { largeur: 240, fps: 10 };  // small and fast to render, like the desktop app
const FULL = { largeur: 480, fps: 12 };   // the GIF that is shared: the CLI's defaults
const DEFAULT_BEFORE = 0.2;
const DEFAULT_AFTER = 0.4;
const GIF_WORKERS = 2;                    // the server renders 2 GIFs at a time (MAX_FFMPEG)
const MAX_RETRIES = 3;                    // on 503: the server is busy

const $ = (id) => document.getElementById(id);

class KeyRejected extends Error {}
class ApiError extends Error {
  constructor(message, status, retryAfter) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

// --- Key ---------------------------------------------------------------------------------------

function storedKey() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;  // storage blocked (private browsing)
  }
}

function storeKey(key) {
  try {
    if (key) localStorage.setItem(STORAGE_KEY, key);
    else localStorage.removeItem(STORAGE_KEY);
    return true;
  } catch {
    return false;
  }
}

let apiKey = storedKey();

// --- API ---------------------------------------------------------------------------------------

function errorMessage(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {  // FastAPI validation error: one entry per invalid parameter
    return detail.map((d) => `${(d.loc || []).slice(-1)[0]} : ${d.msg}`).join(", ");
  }
  return `Erreur du serveur (HTTP ${status}).`;
}

async function call(path, { signal, key = apiKey } = {}) {
  let response;
  try {
    response = await fetch(path, { headers: { "X-API-Key": key }, signal });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Pas de connexion au serveur.", 0);
  }
  if (response.status === 401) throw new KeyRejected("Clé refusée par le serveur.");
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const retryAfter = Number(response.headers.get("Retry-After")) || 0;
    throw new ApiError(errorMessage(body, response.status), response.status, retryAfter);
  }
  return response;
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("Annulé", "AbortError"));
    }, { once: true });
  });
}

/** Downloads a GIF, waiting and retrying while the server is busy (503). */
async function fetchGif(id, params, signal) {
  const query = new URLSearchParams(params).toString();
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await call(`/repliques/${id}/gif?${query}`, { signal });
      const disposition = response.headers.get("Content-Disposition") || "";
      const name = /filename="([^"]+)"/.exec(disposition)?.[1] || `kaamelott-${id}.gif`;
      return { blob: await response.blob(), name };
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 503 || attempt >= MAX_RETRIES) throw e;
      await sleep(Math.min(e.retryAfter || 5, 10) * 1000, signal);
    }
  }
}

function search(query, signal) {
  const params = new URLSearchParams({ q: query, limit: MAX_RESULTS });
  return call(`/repliques?${params}`, { signal }).then((r) => r.json());
}

// --- Screens -----------------------------------------------------------------------------------

function isStandalone() {
  return navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
}

function isIOS() {
  return /iPhone|iPad|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);  // iPadOS
}

function showKeyScreen(message = "") {
  stopSearch();
  $("search-screen").hidden = true;
  $("change-key").hidden = true;
  $("key-screen").hidden = false;
  $("cancel-key").hidden = !apiKey;
  // The installed app has its own storage: a key typed in Safari would not follow
  $("install-hint").hidden = !(isIOS() && !isStandalone());
  $("key-input").value = "";
  setStatus("key-status", message, Boolean(message));
}

function showSearchScreen() {
  $("key-screen").hidden = true;
  $("search-screen").hidden = false;
  $("change-key").hidden = false;
}

function setStatus(id, text, isError = false) {
  const el = $(id);
  el.textContent = text;
  el.classList.toggle("error", isError);
}

function onKeyRejected() {
  storeKey(null);
  apiKey = null;
  closeDetail();
  showKeyScreen("Clé refusée par le serveur : saisis-la à nouveau.");
}

$("key-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = $("key-input").value.trim();
  if (!key) return;
  const button = event.submitter || $("key-form").querySelector("button");
  button.disabled = true;
  setStatus("key-status", "Vérification…");
  try {
    await call("/repliques?q=graal&limit=1", { key });
  } catch (e) {
    // 503 and others come after the key check: the key itself is valid
    if (e instanceof KeyRejected || (e instanceof ApiError && e.status === 0)) {
      setStatus("key-status", e instanceof KeyRejected ? "Clé refusée par le serveur." : e.message, true);
      button.disabled = false;
      return;
    }
  }
  button.disabled = false;
  if (!storeKey(key)) {
    setStatus("key-status", "Impossible d'enregistrer la clé sur cet appareil "
      + "(navigation privée ?). Elle servira jusqu'à la fermeture de l'app.", true);
  }
  apiKey = key;
  navigator.storage?.persist?.().catch(() => {});  // asks the browser not to evict the key
  showSearchScreen();
  $("search-input").focus();
});

$("change-key").addEventListener("click", () => showKeyScreen());
$("cancel-key").addEventListener("click", showSearchScreen);

// --- Search and thumbnails ---------------------------------------------------------------------

let searchController = null;
let objectUrls = [];

function stopSearch() {
  searchController?.abort();
  searchController = null;
  objectUrls.forEach((url) => URL.revokeObjectURL(url));
  objectUrls = [];
}

function formatTime(seconds) {
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function episodeLabel(r) {
  const title = r.titre.replace(/^\d+\s+/, "").replace(/\s+/g, " ").trim();
  return `Livre ${r.livre}, ép. ${r.numero}${title ? ` · ${title}` : ""} · ${formatTime(r.debut)}`;
}

function makeCard(result) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "card";

  const frame = document.createElement("div");
  frame.className = "thumb";
  const loading = document.createElement("span");
  loading.className = "loading";
  loading.textContent = "GIF…";
  frame.append(loading);

  const text = document.createElement("span");
  text.className = "quote";
  text.textContent = result.texte;
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = episodeLabel(result);

  button.append(frame, text, meta);
  button.addEventListener("click", () => openDetail(result, item.thumbUrl));
  item.append(button);
  item.frame = frame;
  item.loading = loading;
  return item;
}

async function loadThumbnails(queue, signal) {
  async function worker() {
    while (queue.length && !signal.aborted) {
      const { result, item } = queue.shift();
      try {
        const { blob } = await fetchGif(result.id, THUMB, signal);
        const url = URL.createObjectURL(blob);
        objectUrls.push(url);
        const img = document.createElement("img");
        img.src = url;
        img.alt = result.texte;
        item.thumbUrl = url;
        item.frame.replaceChildren(img);
      } catch (e) {
        if (e.name === "AbortError") return;
        if (e instanceof KeyRejected) return onKeyRejected();
        item.loading.textContent = "GIF indisponible";
        item.loading.title = e.message;
      }
    }
  }
  await Promise.all(Array.from({ length: GIF_WORKERS }, worker));
}

$("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = $("search-input").value.trim();
  if (!query) return;
  $("search-input").blur();  // hides the iPhone keyboard to show the results
  stopSearch();
  const controller = new AbortController();
  searchController = controller;
  $("results").replaceChildren();
  setStatus("search-status", "Recherche…");
  let results;
  try {
    results = await search(query, controller.signal);
  } catch (e) {
    if (e.name === "AbortError") return;
    if (e instanceof KeyRejected) return onKeyRejected();
    return setStatus("search-status", e.message, true);
  }
  if (!results.length) return setStatus("search-status", "Aucune réplique trouvée.");
  setStatus("search-status", results.length === 1 ? "1 réplique" : `${results.length} répliques`);
  const queue = results.map((result) => ({ result, item: makeCard(result) }));
  $("results").replaceChildren(...queue.map((q) => q.item));
  loadThumbnails(queue, controller.signal);
});

// --- Detail: full-size GIF, share, settings ----------------------------------------------------

const dialog = $("detail");
let current = null;  // { result, controller, url, file }

function closeDetail() {
  if (dialog.open) dialog.close();
}

function canShareFiles(file) {
  try {
    return Boolean(navigator.canShare?.({ files: [file] }));
  } catch {
    return false;
  }
}

async function loadDetail() {
  const state = current;
  state.controller?.abort();
  state.controller = new AbortController();
  state.file = null;
  $("share").disabled = true;
  $("download").hidden = true;
  $("detail-loading").hidden = false;
  $("detail-loading").textContent = "Génération du GIF…";
  setStatus("detail-status", "");

  const text = $("opt-text").value.trim();
  const params = {
    ...FULL,
    avant: Number($("opt-before").value) || 0,
    apres: Number($("opt-after").value) || 0,
  };
  if (text && text !== state.result.texte) params.texte = text;

  try {
    const { blob, name } = await fetchGif(state.result.id, params, state.controller.signal);
    if (current !== state) return;
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = URL.createObjectURL(blob);
    state.file = new File([blob], name, { type: "image/gif" });
    $("detail-img").src = state.url;
    $("detail-loading").hidden = true;
    if (canShareFiles(state.file)) {
      $("share").hidden = false;
      $("share").disabled = false;
    } else {  // computers without a share sheet for files: plain download
      $("share").hidden = true;
      $("download").href = state.url;
      $("download").download = name;
      $("download").hidden = false;
    }
  } catch (e) {
    if (e.name === "AbortError" || current !== state) return;
    if (e instanceof KeyRejected) return onKeyRejected();
    $("detail-loading").textContent = "GIF indisponible";
    setStatus("detail-status", e.message, true);
  }
}

function openDetail(result, thumbUrl) {
  closeDetail();
  current = { result, controller: null, url: null, file: null };
  // The thumbnail is shown, stretched, while the full-size GIF renders
  if (thumbUrl) $("detail-img").src = thumbUrl;
  else $("detail-img").removeAttribute("src");
  $("detail-img").alt = result.texte;
  $("detail-meta").textContent = episodeLabel(result);
  $("opt-text").value = result.texte;
  $("opt-before").value = DEFAULT_BEFORE;
  $("opt-after").value = DEFAULT_AFTER;
  dialog.querySelector("details").open = false;
  dialog.showModal();
  loadDetail();
}

dialog.addEventListener("close", () => {
  if (!current) return;
  current.controller?.abort();
  if (current.url) URL.revokeObjectURL(current.url);
  current = null;
  $("detail-img").removeAttribute("src");
});

// A tap on the dimmed background closes the sheet
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) closeDetail();
});

$("settings").addEventListener("submit", (event) => {
  event.preventDefault();
  if (current) loadDetail();
});

$("share").addEventListener("click", async () => {
  if (!current?.file) return;
  try {
    // Files only: with a text, some apps (WhatsApp) send the text and drop the GIF
    await navigator.share({ files: [current.file] });
  } catch (e) {
    if (e.name !== "AbortError") setStatus("detail-status", `Partage impossible : ${e.message}`, true);
  }
});

// --- Start -------------------------------------------------------------------------------------

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

if (apiKey) {
  showSearchScreen();
} else {
  showKeyScreen();
}
