// SwiftGet Browser Integration - background service worker.
// Captures browser downloads and hands them to the SwiftGet app running on
// this computer (http://127.0.0.1:9614). If the app is not running, the
// browser simply continues the download itself.

const API = "http://127.0.0.1:9614";
const api = globalThis.browser ?? chrome;

const FILE_EXTS = new Set((
  "zip rar 7z gz tar bz2 xz tgz iso cab lz zst " +
  "pdf doc docx xls xlsx ppt pptx rtf odt ods odp epub " +
  "mp3 wav flac aac ogg m4a wma opus aif aiff " +
  "exe msi apk dmg deb rpm appimage jar msix " +
  "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts bin img"
).split(" "));

const DEFAULTS = { enabled: true, minSizeKB: 0, captureAllTypes: false, skipExts: "" };

async function settings() {
  return { ...DEFAULTS, ...(await api.storage.local.get(Object.keys(DEFAULTS))) };
}

// ---------------------------------------------------------------- bridge
async function call(path, body) {
  const opts = { method: body ? "POST" : "GET", headers: { "X-SwiftGet": "1" } };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 3000);
  try {
    const r = await fetch(API + path, { ...opts, signal: ctrl.signal });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  } finally {
    clearTimeout(t);
  }
}

async function appAvailable() {
  try {
    const p = await call("/ping");
    setBadge(p.enabled ? "" : "off");
    return p.enabled;
  } catch {
    setBadge("off");
    return false;
  }
}

function setBadge(text) {
  api.action.setBadgeText({ text });
  if (text) api.action.setBadgeBackgroundColor({ color: "#cf222e" });
}

async function cookieHeader(url) {
  try {
    const list = await api.cookies.getAll({ url });
    return list.map(c => `${c.name}=${c.value}`).join("; ");
  } catch {
    return "";
  }
}

async function sendOne({ url, filename = "", referrer = "", fileSize = -1, mime = "" }) {
  return call("/add", {
    url, filename, referrer, fileSize, mime,
    cookies: await cookieHeader(url),
    userAgent: navigator.userAgent,
  });
}

// ---------------------------------------------------------------- capture
function extOf(url, filename) {
  const name = (filename || "").split(/[\\/]/).pop() ||
               decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
  const m = name.toLowerCase().match(/\.([a-z0-9]{1,10})$/);
  return m ? m[1] : "";
}

function looksLikeFile(mime) {
  mime = (mime || "").toLowerCase();
  if (!mime || mime.startsWith("text/") || mime.includes("html") || mime.startsWith("image/")) return false;
  return mime.startsWith("video/") || mime.startsWith("audio/") || mime.startsWith("application/");
}

async function shouldCapture(item, s) {
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url)) return false;              // blob:, data:, file: cannot be handed off
  if (item.byExtensionId === api.runtime.id) return false;
  const ext = extOf(url, item.filename);
  const skip = s.skipExts.toLowerCase().split(/[\s,;]+/).filter(Boolean);
  if (ext && skip.includes(ext)) return false;
  if (s.minSizeKB > 0 && item.totalBytes > 0 && item.totalBytes < s.minSizeKB * 1024) return false;
  if (s.captureAllTypes) return true;
  return FILE_EXTS.has(ext) || looksLikeFile(item.mime);
}

api.downloads.onCreated.addListener(async (item) => {
  const s = await settings();
  if (!s.enabled || !(await shouldCapture(item, s))) return;

  // hold the browser download while we ask SwiftGet to take it
  try { await api.downloads.pause(item.id); } catch {}
  try {
    await sendOne({
      url: item.finalUrl || item.url,
      filename: (item.filename || "").split(/[\\/]/).pop(),
      referrer: item.referrer || "",
      fileSize: item.totalBytes || item.fileSize || -1,
      mime: item.mime || "",
    });
    try { await api.downloads.cancel(item.id); } catch {}
    try { await api.downloads.erase({ id: item.id }); } catch {}
    setBadge("");
  } catch (e) {
    // SwiftGet not running / disabled - let the browser finish the download
    try { await api.downloads.resume(item.id); } catch {}
    setBadge("off");
  }
});

// ---------------------------------------------------------------- context menus
function createMenus() {
  api.contextMenus.removeAll(() => {
    api.contextMenus.create({ id: "sg-link", title: "Download with SwiftGet", contexts: ["link"] });
    api.contextMenus.create({ id: "sg-media", title: "Download this media with SwiftGet",
                              contexts: ["video", "audio", "image"] });
    api.contextMenus.create({ id: "sg-all", title: "Download all links with SwiftGet",
                              contexts: ["page", "selection"] });
  });
}
api.runtime.onInstalled.addListener(createMenus);
api.runtime.onStartup.addListener(createMenus);

async function notifyFailure(tabId, msg) {
  try {
    await api.scripting.executeScript({
      target: { tabId },
      func: (m) => alert(m),
      args: [msg],
    });
  } catch {}
}

const NOT_RUNNING = "SwiftGet is not running (or browser integration is turned off in SwiftGet > Options > Browser).\n\nStart SwiftGet and try again.";

api.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    if (info.menuItemId === "sg-link" || info.menuItemId === "sg-media") {
      const url = info.menuItemId === "sg-link" ? info.linkUrl : info.srcUrl;
      if (!/^https?:/i.test(url || "")) {
        return notifyFailure(tab.id, "This item cannot be downloaded by SwiftGet (it is not an http/https link).");
      }
      await sendOne({ url, referrer: info.pageUrl || "" });
    } else if (info.menuItemId === "sg-all") {
      await sendAllLinks(tab, info.menuItemId === "sg-all" && !!info.selectionText);
    }
  } catch (e) {
    notifyFailure(tab.id, NOT_RUNNING + "\n\n(" + e.message + ")");
  }
});

// collect links from the page (or only the current selection)
async function sendAllLinks(tab, selectionOnly = false) {
  const [{ result }] = await api.scripting.executeScript({
    target: { tabId: tab.id, allFrames: false },
    args: [selectionOnly],
    func: (selectionOnly) => {
      let anchors = [...document.querySelectorAll("a[href]")];
      const sel = window.getSelection();
      if (selectionOnly && sel && sel.rangeCount) {
        anchors = anchors.filter(a => sel.containsNode(a, true));
      }
      const media = [...document.querySelectorAll("video[src], audio[src], video source[src], audio source[src]")]
        .map(m => ({ url: m.src, text: "media" }));
      const seen = new Set();
      return anchors.map(a => ({ url: a.href, text: (a.textContent || "").trim() }))
        .concat(media)
        .filter(l => /^https?:/i.test(l.url) && !seen.has(l.url) && seen.add(l.url));
    },
  });
  const items = (result || []).filter(l => {
    try { return FILE_EXTS.has(extOf(l.url)); } catch { return false; }
  });
  if (!items.length) {
    return notifyFailure(tab.id, "SwiftGet: no downloadable files were found on this page.");
  }
  await call("/batch", {
    items,
    referrer: tab.url,
    cookies: await cookieHeader(tab.url),
    userAgent: navigator.userAgent,
  });
}

// ---------------------------------------------------------------- popup messages
api.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    if (msg.type === "status") reply({ running: await appAvailable(), ...(await settings()) });
    else if (msg.type === "grabAll") {
      try { await sendAllLinks(msg.tab); reply({ ok: true }); }
      catch (e) { reply({ ok: false, error: e.message }); }
    } else if (msg.type === "open") {
      try { await call("/show", {}); reply({ ok: true }); } catch (e) { reply({ ok: false }); }
    }
  })();
  return true;
});
