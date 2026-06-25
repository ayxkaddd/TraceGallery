const API_BASE = window.location.origin;
const DEFAULT_ROOT_KEY = "galleryDlDefaultRootDir";
const today = new Date();

const optionLabels = {
  images: "Download images",
  videos: "Download videos",
  metadata: "Write metadata",
  infoJson: "Write info JSON",
  captions: "Write captions/descriptions",
  archive: "Use download archive",
  continueArchive: "Continue previous archive",
  skipExisting: "Skip existing files",
  restrictFilenames: "Restrict filenames",
  saveUnsupported: "Save unsupported URLs into error list"
};

const cookieHintRules = [
  { pattern: /(^|\.)boosty\.to$/i, label: "cookies required", reason: "gallery-dl lists Boosty authentication as Cookies." },
  { pattern: /(^|\.)facebook\.com$/i, label: "cookies required", reason: "gallery-dl lists Facebook authentication as Cookies." },
  { pattern: /(^|\.)fantia\.jp$/i, label: "cookies required", reason: "gallery-dl lists Fantia authentication as Cookies." },
  { pattern: /(^|\.)furaffinity\.net$/i, label: "cookies required", reason: "gallery-dl lists Fur Affinity authentication as Cookies." },
  { pattern: /(^|\.)instagram\.com$/i, label: "cookies required", reason: "gallery-dl lists Instagram authentication as Cookies." },
  { pattern: /(^|\.)patreon\.com$/i, label: "cookies required", reason: "gallery-dl lists Patreon authentication as Cookies." },
  { pattern: /(^|\.)pinterest\.com$/i, label: "cookies required", reason: "gallery-dl lists Pinterest authentication as Cookies." },
  { pattern: /(^|\.)fanbox\.cc$/i, label: "cookies required", reason: "gallery-dl lists pixivFANBOX authentication as Cookies." },
  { pattern: /(^|\.)poipiku\.com$/i, label: "cookies required", reason: "gallery-dl lists Poipiku authentication as Cookies." },
  { pattern: /(^|\.)tiktok\.com$/i, label: "cookies required", reason: "gallery-dl lists TikTok authentication as Cookies." },
  { pattern: /(^|\.)x\.com$/i, label: "cookies required", reason: "gallery-dl lists Twitter/X authentication as Cookies." },
  { pattern: /(^|\.)twitter\.com$/i, label: "cookies required", reason: "Twitter URLs are handled by the gallery-dl Twitter/X extractor, which lists Cookies authentication." }
];

const state = {
  urls: [],
  outputDir: "",
  defaultRootDir: "",
  createOutputDir: true,
  caseName: "",
  targetLabel: "",
  notes: "",
  folderMode: "case-target-date",
  cookieMode: "none",
  cookiesFile: "",
  browserProfile: null,
  options: {
    images: true,
    videos: true,
    metadata: true,
    infoJson: true,
    captions: false,
    archive: true,
    continueArchive: true,
    skipExisting: true,
    restrictFilenames: true,
    saveUnsupported: true
  },
  advanced: {
    userAgent: "",
    proxy: "",
    sleepDelay: "",
    rateLimit: "",
    filenameTemplate: "",
    directoryTemplate: "",
    extraArgs: ""
  },
  currentJobId: "",
  finalOutputDir: "",
  commandPreview: "",
  configPreview: "",
  logs: [],
  eventSource: null,
  renderTimer: null,
  library: {
    rootId: "",
    path: "",
    items: [],
    counts: {},
    totalSizeLabel: "",
    filter: "all",
    selected: null,
    scanning: false
  }
};

const $ = (id) => document.getElementById(id);

function onClick(id, handler) {
  const element = $(id);
  if (!element) return;
  element.addEventListener("click", (event) => {
    event.preventDefault();
    handler(event);
  });
}

function getDefaultRootDir() {
  return localStorage.getItem(DEFAULT_ROOT_KEY) || "";
}

function slug(value, fallback) {
  const cleaned = String(value || "").trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-._]+|[-._]+$/g, "");
  return cleaned || fallback;
}

function dateStamp() {
  const pad = (value) => String(value).padStart(2, "0");
  return `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
}

function dateTimeStamp() {
  const pad = (value) => String(value).padStart(2, "0");
  return `${dateStamp()}_${pad(today.getHours())}-${pad(today.getMinutes())}-${pad(today.getSeconds())}`;
}

function splitUrls(value) {
  return value
    .split(/[\n\r\t ,]+/)
    .map((url) => url.trim())
    .filter(Boolean);
}

function looksLikeUrl(value) {
  return /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(value);
}

function cookieHintForUrl(value) {
  try {
    const host = new URL(value).hostname.replace(/^www\./i, "");
    return cookieHintRules.find((rule) => rule.pattern.test(host)) || null;
  } catch {
    return null;
  }
}

function getCookieMode() {
  return document.querySelector("input[name='cookieMode']:checked")?.value || "none";
}

function collectStateFromInputs() {
  state.outputDir = $("outputDir").value.trim();
  state.createOutputDir = $("createOutputDir").checked;
  state.caseName = $("caseName").value.trim();
  state.targetLabel = $("targetLabel").value.trim();
  state.notes = $("notes").value.trim();
  state.folderMode = $("folderMode").value;
  state.cookieMode = getCookieMode();
  state.cookiesFile = $("cookiesFile").value.trim();
  state.advanced.userAgent = $("userAgent").value.trim();
  state.advanced.proxy = $("proxy").value.trim();
  state.advanced.sleepDelay = $("sleepDelay").value.trim();
  state.advanced.rateLimit = $("rateLimit").value.trim();
  state.advanced.filenameTemplate = $("filenameTemplate").value.trim();
  state.advanced.directoryTemplate = $("directoryTemplate").value.trim();
  state.advanced.extraArgs = $("extraArgs").value.trim();

  const selectedProfile = $("browserProfile").value;
  state.browserProfile = selectedProfile ? JSON.parse(selectedProfile) : null;
}

function jobPayload() {
  collectStateFromInputs();
  return {
    urls: state.urls,
    outputDir: state.outputDir,
    createOutputDir: state.createOutputDir,
    caseName: state.caseName,
    targetLabel: state.targetLabel,
    notes: state.notes,
    folderMode: state.folderMode,
    cookieMode: state.cookieMode,
    cookiesFile: state.cookiesFile,
    browserProfile: state.browserProfile,
    options: state.options,
    advanced: state.advanced
  };
}

function localFinalPathPreview() {
  if (!state.outputDir) return "Select an output directory";
  const pieces = [state.outputDir.replace(/\/+$/, "")];
  if (state.folderMode !== "flat") {
    pieces.push(slug(state.caseName, "case"), slug(state.targetLabel, "target"));
  }
  if (state.folderMode === "case-target-date") pieces.push(dateStamp());
  if (state.folderMode === "case-target-datetime") pieces.push(dateTimeStamp());
  return pieces.join("/");
}

function validateForm() {
  const warnings = [];
  if (!state.urls.length) warnings.push("Add at least one target URL.");
  const invalid = state.urls.filter((url) => !looksLikeUrl(url));
  if (invalid.length) warnings.push(`Some URLs may be invalid: ${invalid.slice(0, 2).join(", ")}`);
  if (!state.outputDir) warnings.push("Choose or enter an output directory.");
  if (state.cookieMode === "file" && !state.cookiesFile) warnings.push("Enter a cookies.txt path or choose No cookies.");
  if (state.cookieMode === "browser" && !state.browserProfile) warnings.push("Detect and select a browser profile first.");
  return warnings;
}

function renderUrls() {
  $("urlList").innerHTML = "";
  state.urls.forEach((url, index) => {
    const hint = cookieHintForUrl(url);
    const row = document.createElement("div");
    row.className = "flex items-start gap-2 rounded border border-line bg-slate-950 p-2";
    const body = document.createElement("div");
    body.className = "min-w-0 flex-1";
    const text = document.createElement("div");
    text.className = `break-all font-mono text-sm ${looksLikeUrl(url) ? "text-slate-200" : "text-amber-200"}`;
    text.textContent = url;
    body.append(text);
    if (hint) {
      const tag = document.createElement("span");
      tag.className = "mt-2 inline-flex rounded border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-200";
      tag.textContent = hint.label;
      tag.title = hint.reason;
      body.append(tag);
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rounded border border-line px-2 py-1 text-xs text-muted";
    button.textContent = "Remove";
    button.addEventListener("click", () => {
      state.urls.splice(index, 1);
      render();
    });
    row.append(body, button);
    $("urlList").append(row);
  });
}

function renderOptions() {
  const grid = $("optionsGrid");
  grid.innerHTML = "";
  Object.entries(optionLabels).forEach(([key, label]) => {
    const wrapper = document.createElement("label");
    wrapper.className = "flex items-center gap-2 rounded border border-line bg-slate-950 px-3 py-2";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "accent-sky-400";
    checkbox.checked = state.options[key];
    checkbox.addEventListener("change", () => {
      state.options[key] = checkbox.checked;
      render();
    });
    const span = document.createElement("span");
    span.textContent = label;
    wrapper.append(checkbox, span);
    grid.append(wrapper);
  });
}

function renderPresets() {
  const presets = JSON.parse(localStorage.getItem("galleryDlPresets") || "{}");
  const list = $("presetList");
  list.innerHTML = "";
  Object.keys(presets).sort().forEach((name) => {
    const load = document.createElement("button");
    load.className = "rounded border border-line px-3 py-1.5 text-sm";
    load.textContent = name;
    load.addEventListener("click", () => loadPreset(name));
    const del = document.createElement("button");
    del.className = "rounded border border-red-900 px-2 py-1.5 text-sm text-red-200";
    del.textContent = "Delete";
    del.addEventListener("click", () => deletePreset(name));
    list.append(load, del);
  });
}

function mediaUrl(item) {
  return `${API_BASE}${item.url}`;
}

function renderLibrary() {
  const stats = $("libraryStats");
  const grid = $("libraryGrid");
  const detail = $("libraryDetail");
  const dialogPath = $("libraryDialogPath");
  const viewer = $("libraryViewer");
  const library = state.library;
  const items = library.filter === "all" ? library.items : library.items.filter((item) => item.kind === library.filter);

  stats.textContent = library.rootId
    ? `${library.items.length} files, ${library.totalSizeLabel}`
    : "No folder scanned";
  dialogPath.textContent = library.path || "";
  viewer.classList.toggle("hidden", !library.rootId);

  document.querySelectorAll(".libraryFilter").forEach((button) => {
    const active = button.dataset.filter === library.filter;
    button.className = active
      ? "libraryFilter rounded bg-sky-500 px-3 py-1.5 font-medium text-slate-950"
      : "libraryFilter rounded border border-line px-3 py-1.5";
  });

  grid.innerHTML = "";
  if (!library.rootId) {
    grid.innerHTML = '<div class="rounded border border-line bg-slate-950 p-3 text-sm text-muted">Scan an archive folder to view saved media.</div>';
  } else if (!items.length) {
    grid.innerHTML = '<div class="rounded border border-line bg-slate-950 p-3 text-sm text-muted">No files match this filter.</div>';
  }

  items.forEach((item) => {
    const selected = library.selected?.relativePath === item.relativePath;
    const card = document.createElement("button");
    card.type = "button";
    card.className = selected
      ? "grid min-w-0 grid-cols-[88px_minmax(0,1fr)] overflow-hidden rounded border border-accent bg-slate-900 text-left"
      : "grid min-w-0 grid-cols-[88px_minmax(0,1fr)] overflow-hidden rounded border border-line bg-slate-950 text-left hover:border-accent";
    card.addEventListener("click", () => {
      state.library.selected = item;
      renderLibrary();
    });

    const preview = document.createElement("div");
    preview.className = "flex h-16 w-[88px] items-center justify-center overflow-hidden bg-black";
    if (item.kind === "image") {
      const image = document.createElement("img");
      image.src = mediaUrl(item);
      image.loading = "lazy";
      image.className = "h-full w-full object-cover";
      preview.append(image);
    } else if (item.kind === "video") {
      const video = document.createElement("video");
      video.src = mediaUrl(item);
      video.className = "h-full w-full object-cover";
      video.muted = true;
      video.preload = "metadata";
      preview.append(video);
    } else {
      const icon = document.createElement("div");
      icon.className = "text-sm uppercase tracking-wide text-muted";
      icon.textContent = item.kind;
      preview.append(icon);
    }

    const info = document.createElement("div");
    info.className = "min-w-0 space-y-1 p-2";
    const name = document.createElement("div");
    name.className = "truncate font-mono text-xs text-slate-200";
    name.textContent = item.name;
    const meta = document.createElement("div");
    meta.className = "text-xs text-muted";
    meta.textContent = `${item.kind} · ${item.sizeLabel}`;
    info.append(name, meta);
    card.append(preview, info);
    grid.append(card);
  });

  detail.innerHTML = "";
  if (!library.selected) {
    detail.textContent = "Select a saved item to preview it.";
    return;
  }
  renderLibraryDetail(library.selected, detail);
}

function renderLibraryDetail(item, container) {
  const title = document.createElement("div");
  title.className = "mb-3 break-all font-mono text-sm text-slate-200";
  title.textContent = item.relativePath;
  container.append(title);

  if ((item.kind === "image" || item.kind === "video") && item.summary?.originalUrl) {
    const original = document.createElement("a");
    original.href = item.summary.originalUrl;
    original.target = "_blank";
    original.rel = "noopener noreferrer";
    original.className = "mb-3 inline-flex max-w-full items-center rounded border border-line px-3 py-1.5 text-sm text-sky-300 hover:border-accent hover:text-sky-200";
    original.textContent = "Original post";
    original.title = item.summary.originalUrl;
    container.append(original);
  }

  if (item.kind === "image") {
    const image = document.createElement("img");
    image.src = mediaUrl(item);
    image.className = "mx-auto max-h-[48vh] max-w-full rounded border border-line object-contain";
    container.append(image);
  } else if (item.kind === "video") {
    const video = document.createElement("video");
    video.src = mediaUrl(item);
    video.className = "mx-auto max-h-[48vh] max-w-full rounded border border-line bg-black";
    video.controls = true;
    video.preload = "metadata";
    container.append(video);
  } else if (item.kind === "audio") {
    const audio = document.createElement("audio");
    audio.src = mediaUrl(item);
    audio.className = "w-full";
    audio.controls = true;
    container.append(audio);
  } else if (item.kind === "metadata" || item.kind === "text") {
    const link = document.createElement("a");
    link.href = mediaUrl(item);
    link.target = "_blank";
    link.className = "text-sky-300 underline";
    link.textContent = "Open file";
    container.append(link);
  }

  const facts = document.createElement("div");
  facts.className = "mt-3 grid grid-cols-1 gap-2 text-xs text-muted sm:grid-cols-2";
  facts.innerHTML = `
    <div><span class="text-slate-400">Type:</span> ${item.kind}</div>
    <div><span class="text-slate-400">Size:</span> ${item.sizeLabel}</div>
    <div><span class="text-slate-400">Modified:</span> ${item.modified}</div>
    <div><span class="text-slate-400">MIME:</span> ${item.mime}</div>
  `;
  container.append(facts);
}

function render() {
  collectStateFromInputs();
  state.defaultRootDir = getDefaultRootDir();
  renderUrls();
  renderOptions();
  $("defaultRootHint").textContent = state.defaultRootDir
    ? `Default root: ${state.defaultRootDir}`
    : "Set a default root to prefill this folder for future cases.";
  $("pathPreview").textContent = state.finalOutputDir || localFinalPathPreview();
  const invalidUrlWarning = state.urls.some((url) => !looksLikeUrl(url)) ? "One or more URLs do not look complete. The backend will validate before running." : "";
  const cookieHintCount = state.urls.filter((url) => cookieHintForUrl(url)).length;
  const cookieWarning = cookieHintCount && state.cookieMode === "none" ? `${cookieHintCount} target${cookieHintCount === 1 ? "" : "s"} may need cookies for reliable archiving.` : "";
  $("urlWarning").textContent = [invalidUrlWarning, cookieWarning].filter(Boolean).join(" ");
  $("formWarning").textContent = validateForm().join(" ");
  $("commandPreview").textContent = state.commandPreview || "Add targets and an output directory to preview the command.";
  $("configPreview").textContent = state.configPreview || "{}";
  $("logs").textContent = state.logs.join("\n");
  $("logs").scrollTop = $("logs").scrollHeight;
  if (!$("libraryPath").value && (state.finalOutputDir || state.outputDir)) {
    $("libraryPath").value = state.finalOutputDir || localFinalPathPreview();
  }
  renderPresets();
  renderLibrary();
  schedulePreview();
}

function schedulePreview() {
  clearTimeout(state.renderTimer);
  state.renderTimer = setTimeout(updateBackendPreview, 250);
}

async function updateBackendPreview() {
  const warnings = validateForm();
  if (warnings.some((warning) => warning.startsWith("Add at least") || warning.startsWith("Choose"))) {
    state.finalOutputDir = "";
    $("pathPreview").textContent = localFinalPathPreview();
    return;
  }
  try {
    const response = await fetch(`${API_BASE}/api/config/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(jobPayload())
    });
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json();
    state.commandPreview = data.command;
    state.configPreview = data.configJson;
    state.finalOutputDir = data.finalOutputDir;
    $("commandPreview").textContent = state.commandPreview;
    $("configPreview").textContent = state.configPreview;
    $("pathPreview").textContent = state.finalOutputDir;
  } catch (error) {
    $("formWarning").textContent = `Preview warning: ${error.message}`;
  }
}

function addUrlsFromInput() {
  const incoming = splitUrls($("urlInput").value);
  incoming.forEach((url) => {
    if (!state.urls.includes(url)) state.urls.push(url);
  });
  $("urlInput").value = "";
  render();
}

async function checkBackend() {
  try {
    const [health, version] = await Promise.all([
      fetch(`${API_BASE}/api/health`).then((response) => response.json()),
      fetch(`${API_BASE}/api/gallery-dl/version`).then((response) => response.json())
    ]);
    $("backendStatus").textContent = `Backend: ${health.status}`;
    $("galleryVersion").textContent = `gallery-dl: ${version.version}`;
  } catch {
    $("backendStatus").textContent = "Backend: offline";
    $("galleryVersion").textContent = "gallery-dl: unknown";
  }
}

async function startJob() {
  const warnings = validateForm();
  if (warnings.length) {
    $("formWarning").textContent = warnings.join(" ");
    return;
  }
  const response = await fetch(`${API_BASE}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(jobPayload())
  });
  if (!response.ok) {
    $("formWarning").textContent = await response.text();
    return;
  }
  const data = await response.json();
  state.currentJobId = data.jobId;
  state.logs = [`Job ${data.jobId} created.`];
  $("jobStatus").textContent = "Running";
  connectLogStream(data.jobId);
  render();
}

async function stopJob() {
  if (!state.currentJobId) return;
  await fetch(`${API_BASE}/api/jobs/${state.currentJobId}/stop`, { method: "POST" });
  $("jobStatus").textContent = "Stopping";
}

function connectLogStream(jobId) {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = new EventSource(`${API_BASE}/api/jobs/${jobId}/events`);
  state.eventSource.onmessage = (event) => {
    state.logs.push(JSON.parse(event.data));
    $("logs").textContent = state.logs.join("\n");
    $("logs").scrollTop = $("logs").scrollHeight;
  };
  state.eventSource.addEventListener("done", (event) => {
    const status = JSON.parse(event.data);
    $("jobStatus").textContent = `${status.status} (${status.exitCode ?? "no exit code"})`;
    if (status.outputDir) $("libraryPath").value = status.outputDir;
    state.eventSource.close();
  });
  state.eventSource.onerror = () => {
    $("jobStatus").textContent = "Log stream disconnected";
  };
}

async function detectProfiles() {
  const response = await fetch(`${API_BASE}/api/browser-profiles`);
  if (!response.ok) {
    $("formWarning").textContent = "Could not detect browser profiles.";
    return;
  }
  const data = await response.json();
  const select = $("browserProfile");
  select.innerHTML = '<option value="">Select a local browser/profile</option>';
  if (!data.profiles.length) {
    $("formWarning").textContent = data.notice;
    return;
  }
  data.profiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = JSON.stringify(profile);
    option.textContent = profile.label || (profile.profile ? `${profile.browser}: ${profile.profile}` : profile.browser);
    select.append(option);
  });
  $("formWarning").textContent = data.notice;
}

async function openOutputFolder() {
  collectStateFromInputs();
  if (!state.outputDir) {
    $("formWarning").textContent = "Choose or enter an output directory first.";
    return;
  }
  const path = state.finalOutputDir || localFinalPathPreview();
  const response = await fetch(`${API_BASE}/api/paths/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create: $("createOutputDir").checked })
  });
  if (!response.ok) {
    $("formWarning").textContent = await response.text();
    return;
  }
  const data = await response.json();
  $("formWarning").textContent = `Opened ${data.path} with ${data.opener}.`;
}

function saveDefaultRoot() {
  collectStateFromInputs();
  if (!state.outputDir) {
    $("formWarning").textContent = "Enter or pick a root folder before setting it as the default.";
    return;
  }
  localStorage.setItem(DEFAULT_ROOT_KEY, state.outputDir);
  state.defaultRootDir = state.outputDir;
  $("formWarning").textContent = "Default case root saved.";
  render();
}

async function scanLibrary() {
  if (state.library.scanning) return;
  const path = $("libraryPath").value.trim() || state.finalOutputDir || localFinalPathPreview();
  if (!path || path === "Select an output directory") {
    $("formWarning").textContent = "Choose an archive folder to scan.";
    return;
  }
  state.library.scanning = true;
  $("formWarning").textContent = "Scanning saved data...";
  let data;
  try {
    const response = await fetch(`${API_BASE}/api/library/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, maxFiles: 3000, maxDepth: 8 })
    });
    if (!response.ok) {
      $("formWarning").textContent = await response.text();
      return;
    }
    data = await response.json();
  } catch (error) {
    $("formWarning").textContent = `Could not scan saved data: ${error.message}`;
    return;
  } finally {
    state.library.scanning = false;
  }
  state.library.rootId = data.rootId;
  state.library.path = data.path;
  state.library.items = data.items;
  state.library.counts = data.counts;
  state.library.totalSizeLabel = data.totalSizeLabel;
  state.library.selected = data.items.find((item) => item.kind === "video") || data.items.find((item) => item.kind === "image") || data.items[0] || null;
  $("libraryPath").value = data.path;
  const truncatedNote = data.truncated ? ` Showing first ${data.maxFiles} files.` : "";
  $("formWarning").textContent = `Scanned ${data.items.length} files from ${data.path}.${truncatedNote}`;
  renderLibrary();
  $("libraryViewer").scrollIntoView({ behavior: "smooth", block: "start" });
}

function useCurrentOutputForLibrary() {
  collectStateFromInputs();
  const path = state.finalOutputDir || localFinalPathPreview();
  if (!path || path === "Select an output directory") {
    $("formWarning").textContent = "Choose an output directory first.";
    return;
  }
  $("libraryPath").value = path;
  $("formWarning").textContent = "Saved Data will scan the current output folder.";
}

function savePreset() {
  const name = $("presetName").value.trim();
  if (!name) return;
  const presets = JSON.parse(localStorage.getItem("galleryDlPresets") || "{}");
  presets[name] = jobPayload();
  localStorage.setItem("galleryDlPresets", JSON.stringify(presets));
  $("presetName").value = "";
  renderPresets();
}

function loadPreset(name) {
  const presets = JSON.parse(localStorage.getItem("galleryDlPresets") || "{}");
  const preset = presets[name];
  if (!preset) return;
  Object.assign(state, preset);
  const defaultRoot = getDefaultRootDir();
  state.outputDir = state.outputDir || defaultRoot;
  $("outputDir").value = state.outputDir || "";
  $("createOutputDir").checked = state.createOutputDir !== false;
  $("caseName").value = state.caseName || "";
  $("targetLabel").value = state.targetLabel || "";
  $("notes").value = state.notes || "";
  $("folderMode").value = state.folderMode || "case-target-date";
  document.querySelector(`input[name='cookieMode'][value='${state.cookieMode || "none"}']`).checked = true;
  $("cookiesFile").value = state.cookiesFile || "";
  Object.keys(state.advanced).forEach((key) => {
    $(key).value = state.advanced[key] || "";
  });
  render();
}

function deletePreset(name) {
  const presets = JSON.parse(localStorage.getItem("galleryDlPresets") || "{}");
  delete presets[name];
  localStorage.setItem("galleryDlPresets", JSON.stringify(presets));
  renderPresets();
}

function downloadConfig() {
  const blob = new Blob([state.configPreview || "{}"], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "gallery-dl.conf";
  link.click();
  URL.revokeObjectURL(link.href);
}

async function saveConfigThroughBackend() {
  const response = await fetch(`${API_BASE}/api/config/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config: jobPayload(), filename: "gallery-dl.conf" })
  });
  $("formWarning").textContent = response.ok ? `Saved config: ${(await response.json()).path}` : await response.text();
}

async function copyText(value) {
  await navigator.clipboard.writeText(value);
}

function isFirefoxBrowser() {
  return navigator.userAgent.toLowerCase().includes("firefox");
}

async function pickDirectoryInBrowser() {
  const handle = await window.showDirectoryPicker();
  $("outputDir").value = handle.name;
  $("formWarning").textContent = "Browser picker selected this folder name. Browsers do not expose the full local path, so use the backend picker or paste the full path before running.";
  render();
}

async function pickDirectoryPathThroughBackend() {
  $("formWarning").textContent = "Opening local directory picker...";
  const response = await fetch(`${API_BASE}/api/paths/pick`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

async function pickDirectoryThroughBackend() {
  const data = await pickDirectoryPathThroughBackend();
  $("outputDir").value = data.path;
  $("formWarning").textContent = `Selected with ${data.picker}.`;
  render();
}

function hydrateDefaultRoot() {
  state.defaultRootDir = getDefaultRootDir();
  if (state.defaultRootDir && !$("outputDir").value.trim()) {
    $("outputDir").value = state.defaultRootDir;
    state.outputDir = state.defaultRootDir;
  }
}

async function pickLibraryFolder() {
  try {
    const data = await pickDirectoryPathThroughBackend();
    $("libraryPath").value = data.path;
    $("formWarning").textContent = `Saved Data folder selected with ${data.picker}.`;
  } catch (error) {
    $("formWarning").textContent = `Saved Data picker is unavailable: ${error.message}`;
  }
}

function bindInputs() {
  document.querySelectorAll("input, textarea, select").forEach((input) => {
    input.addEventListener("input", render);
    input.addEventListener("change", render);
  });
  $("urlInput").addEventListener("paste", () => setTimeout(addUrlsFromInput, 0));
  onClick("addUrlBtn", addUrlsFromInput);
  onClick("runBtn", startJob);
  onClick("stopBtn", stopJob);
  onClick("clearLogsBtn", () => {
    state.logs = [];
    render();
  });
  onClick("copyLogsBtn", () => copyText(state.logs.join("\n")));
  onClick("copyCommandBtn", () => copyText(state.commandPreview));
  onClick("copyConfigBtn", () => copyText(state.configPreview));
  onClick("downloadConfigBtn", downloadConfig);
  onClick("saveConfigBtn", saveConfigThroughBackend);
  onClick("savePresetBtn", savePreset);
  onClick("saveDefaultRootBtn", saveDefaultRoot);
  onClick("detectProfilesBtn", detectProfiles);
  onClick("openFolderBtn", openOutputFolder);
  onClick("scanLibraryBtn", scanLibrary);
  onClick("libraryPickBtn", pickLibraryFolder);
  onClick("libraryUseOutputBtn", useCurrentOutputForLibrary);
  document.querySelectorAll(".libraryFilter").forEach((button) => {
    button.addEventListener("click", () => {
      state.library.filter = button.dataset.filter;
      renderLibrary();
    });
  });
  onClick("pickDirBtn", async () => {
    const canUseBrowserPicker = window.showDirectoryPicker && !isFirefoxBrowser();
    if (canUseBrowserPicker) {
      try {
        await pickDirectoryInBrowser();
        return;
      } catch (error) {
        if (error.name === "AbortError") return;
        $("formWarning").textContent = `Browser picker failed: ${error.message}`;
      }
    }

    try {
      await pickDirectoryThroughBackend();
      return;
    } catch (error) {
      $("formWarning").textContent = `Backend directory picker is unavailable: ${error.message}`;
    }

    if (!window.showDirectoryPicker || isFirefoxBrowser()) {
      $("formWarning").textContent += " Paste the path manually, or start the backend and open this app from its local URL.";
      return;
    }
    await pickDirectoryInBrowser();
  });

  document.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.id === "scanLibraryBtn") {
      event.preventDefault();
      scanLibrary();
    }
  });
}

function seedPresets() {
  if (localStorage.getItem("galleryDlPresets")) return;
  const base = jobPayload();
  const presets = {
    "Public profile archive": { ...base, options: { ...base.options, archive: true, metadata: true, infoJson: true } },
    "Full metadata archive": { ...base, options: { ...base.options, captions: true, metadata: true, infoJson: true } },
    "Slow safe archive": { ...base, advanced: { ...base.advanced, sleepDelay: "2", rateLimit: "1M" } },
    "Cookies archive": { ...base, cookieMode: "file" }
  };
  localStorage.setItem("galleryDlPresets", JSON.stringify(presets));
}

bindInputs();
hydrateDefaultRoot();
seedPresets();
checkBackend();
renderOptions();
render();
