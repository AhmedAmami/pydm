// Hands browser downloads to PyDM via the native messaging host.
// The browser's own download is only cancelled after PyDM confirms receipt,
// so if PyDM isn't installed or fails, the download carries on as normal.

if (typeof importScripts === "function") importScripts("sites.js");  // PYDM_SITES

const HOST = "com.pydm.host";

const DEFAULTS = {
  enabled: true,
  minSizeKB: 0,
  videoButton: true,
  extensions: [
    "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "dmg",
    "exe", "msi", "apk", "deb", "rpm", "appimage",
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v",
    "mp3", "flac", "wav", "aac", "ogg", "m4a",
    "pdf", "epub", "torrent", "bin",
  ].join(" "),
};

async function getSettings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

function basename(path) {
  return (path || "").split(/[\\/]/).pop();
}

function extensionOf(name) {
  const m = /\.([a-z0-9]{1,10})$/i.exec(name || "");
  return m ? m[1].toLowerCase() : "";
}

async function cookieHeader(url) {
  try {
    const cookies = await chrome.cookies.getAll({ url });
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch {
    return "";
  }
}

async function sendToPyDM(url, filename, referrer, fileSize, kind = "", title = "") {
  const message = {
    type: "download",
    url,
    filename: filename || "",
    referrer: referrer || "",
    cookies: await cookieHeader(url),
    userAgent: navigator.userAgent,
    fileSize: fileSize > 0 ? fileSize : null,
    kind: ["hls", "dash", "ytdl"].includes(kind) ? kind : "",
    title: title || "",
  };
  try {
    const reply = await chrome.runtime.sendNativeMessage(HOST, message);
    if (!reply?.ok) console.warn("PyDM refused download:", reply?.error);
    return !!reply?.ok;
  } catch (e) {
    console.warn("PyDM native host unavailable:", e?.message || e);
    return false;
  }
}

async function shouldCapture(item, name) {
  const s = await getSettings();
  if (!s.enabled) return false;
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url)) return false; // blob:, data:, file: can't be re-fetched
  if (item.byExtensionId) return false;
  const size = item.totalBytes > 0 ? item.totalBytes : item.fileSize;
  if (size > 0 && size < s.minSizeKB * 1024) return false;
  const wanted = new Set(s.extensions.toLowerCase().split(/[\s,]+/).filter(Boolean));
  return wanted.has(extensionOf(name)) || wanted.has(extensionOf(new URL(url).pathname));
}

// onDeterminingFilename fires once the response headers are in, so we know the
// real filename (Content-Disposition) - unlike onCreated.
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  const name = basename(item.filename);
  (async () => {
    let captured = false;
    if (await shouldCapture(item, name)) {
      const size = item.totalBytes > 0 ? item.totalBytes : item.fileSize;
      captured = await sendToPyDM(item.finalUrl || item.url, name, item.referrer, size);
    }
    if (captured) {
      try {
        await chrome.downloads.cancel(item.id);
        await chrome.downloads.erase({ id: item.id });
      } catch (e) {
        console.warn("Could not cancel browser download:", e);
      }
    }
    try {
      suggest();
    } catch {
      // already cancelled; nothing to suggest
    }
  })();
  return true; // we call suggest() asynchronously
});

// --- Video detection -----------------------------------------------------------
// Watch responses for HLS/DASH playlists and large media files, per tab. Kept in
// storage.session because the service worker can be stopped at any time.

const MIN_MEDIA_FILE = 1024 * 1024;
const MAX_PER_TAB = 30;

function mediaKind(url, contentType) {
  const path = new URL(url).pathname.toLowerCase();
  const ct = (contentType || "").toLowerCase();
  if (path.endsWith(".m3u8") || ct.includes("mpegurl")) return "hls";
  if (path.endsWith(".mpd") || ct.includes("dash+xml")) return "dash";
  return null;
}

function header(headers, name) {
  return headers?.find((h) => h.name.toLowerCase() === name)?.value || "";
}

// A master playlist lists one child playlist per quality (and audio track).
// Reading it lets us hide those children, so each video shows up once.
async function hlsChildren(url) {
  try {
    const text = await (await fetch(url, { credentials: "include" })).text();
    if (!text.includes("#EXT-X-STREAM-INF")) return null; // a media playlist
    const uris = [];
    for (const line of text.split(/\r?\n/)) {
      const t = line.trim();
      if (t && !t.startsWith("#")) uris.push(t);
      for (const m of t.matchAll(/URI="([^"]+)"/g)) uris.push(m[1]);
    }
    return uris.map((u) => new URL(u, url).href);
  } catch {
    return null;
  }
}

function visibleMedia(items) {
  const hidden = new Set(items.flatMap((i) => i.children || []));
  return items.filter((i) => !hidden.has(i.url));
}

let storageQueue = Promise.resolve(); // serialise read-modify-write of the per-tab lists

function updateTabMedia(tabId, change) {
  storageQueue = storageQueue.then(async () => {
    const key = `media:${tabId}`;
    const items = (await chrome.storage.session.get(key))[key] || [];
    const next = change(items);
    if (!next) return;
    await chrome.storage.session.set({ [key]: next });
    const count = visibleMedia(next).length;
    await chrome.action.setBadgeText({ tabId, text: count ? String(count) : "" });
    await chrome.action.setBadgeBackgroundColor({ tabId, color: "#1565c0" });
  }).catch((e) => console.warn("PyDM media list:", e));
  return storageQueue;
}

// Facebook's video pieces come from *.fbcdn.net with an "efg" parameter: base64
// JSON that includes the video's id. When the button can't find a feed video's
// post link, the video that just played in the tab gives facebook.com/watch/?v=<id>.
function facebookVideoId(url) {
  try {
    const u = new URL(url);
    if (!/(^|\.)fbcdn\.net$/i.test(u.hostname)) return null;
    const efg = u.searchParams.get("efg");
    if (!efg) return null;
    let b64 = efg.replace(/-/g, "+").replace(/_/g, "/");
    b64 += "=".repeat((4 - (b64.length % 4)) % 4);
    const info = JSON.parse(atob(b64));
    const id = info.video_id || info.xpv_asset_id;
    return id && /^\d{5,}$/.test(String(id)) ? String(id) : null;
  } catch {
    return null;
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const id = facebookVideoId(details.url);
    if (id) chrome.storage.session.set({ [`fbvideo:${details.tabId}`]: { id, time: Date.now() } }).catch(() => {});
  },
  { urls: ["*://*.fbcdn.net/*"], types: ["xmlhttprequest", "media", "other"] },
);

async function recentFacebookVideo(tabId) {
  const key = `fbvideo:${tabId}`;
  const v = (await chrome.storage.session.get(key))[key];
  return v && Date.now() - v.time < 5 * 60 * 1000 ? `https://www.facebook.com/watch/?v=${v.id}` : null;
}

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0 || details.statusCode >= 400 || !/^https?:/i.test(details.url)) return;
    const ct = header(details.responseHeaders, "content-type");
    let kind = mediaKind(details.url, ct);
    const total = parseInt(header(details.responseHeaders, "content-range").split("/")[1], 10)
      || parseInt(header(details.responseHeaders, "content-length"), 10) || 0;
    if (!kind && details.type === "media" && /^(video|audio)\//i.test(ct) && total >= MIN_MEDIA_FILE) {
      kind = "file";
    }
    if (!kind) return;
    (async () => {
      const children = kind === "hls" ? await hlsChildren(details.url) : null;
      await updateTabMedia(details.tabId, (items) => {
        if (items.some((i) => i.url === details.url)) return null;
        const entry = { url: details.url, kind, size: kind === "file" ? total : 0, children, time: Date.now() };
        return [...items, entry].slice(-MAX_PER_TAB);
      });
    })();
  },
  { urls: ["<all_urls>"], types: ["xmlhttprequest", "media", "other"] },
  ["responseHeaders"],
);

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === "loading" && info.url) {
    updateTabMedia(tabId, () => []); // navigated away: start a fresh list
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove([`media:${tabId}`, `fbvideo:${tabId}`]);
});

async function bestMediaForTab(tabId) {
  const items = visibleMedia((await chrome.storage.session.get(`media:${tabId}`))[`media:${tabId}`] || []);
  const rank = (i) => (i.children ? 0 : i.kind === "hls" || i.kind === "dash" ? 1 : 2);
  return items.sort((a, b) => rank(a) - rank(b))[0] || null;
}

async function sendMedia(tabId, url, kind) {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  const isStream = kind === "hls" || kind === "dash"; // YouTube: yt-dlp knows the real title
  return sendToPyDM(url, "", tab?.url, null, kind, isStream ? tab?.title : "");
}

// The popup asks us to send one of the detected videos.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.action !== "download-media") return false;
  sendMedia(msg.tabId, msg.url, msg.kind).then((ok) => sendResponse({ ok }));
  return true; // async response
});

// Right-click "Download with PyDM" on links and media.
chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  const missing = Object.fromEntries(Object.entries(DEFAULTS).filter(([k]) => !(k in stored)));
  await chrome.storage.local.set(missing);
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({
    id: "pydm-download",
    title: "Download with PyDM",
    contexts: ["link", "video", "audio", "image"],
  });
  chrome.contextMenus.create({
    id: "pydm-page-video",
    title: "Download the video on this page with PyDM",
    contexts: ["page"],
    documentUrlPatterns: ["*://*.youtube.com/watch*", "*://*.youtube.com/shorts/*", "*://*.youtube.com/live/*",
      ...PYDM_SITES.domains().flatMap((d) => [`*://${d}/*`, `*://*.${d}/*`])],
  });
});

function notify(message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: "PyDM",
    message,
  }).catch((e) => console.warn("PyDM:", message, e));
}

const NOT_REACHABLE = "Couldn't reach PyDM. Open the PyDM toolbar popup → Settings → Test connection.";

// YouTube's player doesn't fetch plain playlists, so webRequest sees nothing
// useful there. PyDM hands its page links to yt-dlp instead.
function isYouTube(url) {
  try {
    return /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$/i.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

function isYouTubeVideo(url) {
  if (!isYouTube(url)) return false;
  const u = new URL(url);
  return u.hostname.endsWith("youtu.be") ? u.pathname.length > 1
    : (u.pathname === "/watch" && u.searchParams.has("v")) || /^\/(shorts|live|embed)\/[\w-]+/.test(u.pathname);
}

// Shared by the right-click menu and the button on videos (content.js).
// Returns null when the video was handed to PyDM, else a message for the user.
async function downloadFromPage(info, tab) {
  const url = info.linkUrl || info.srcUrl;
  const pageUrl = info.pageUrl || tab?.url || "";
  // yt-dlp (kind "ytdl") gets page links; "" is a plain file link.
  const send = async (link, kind = "ytdl") => ((await sendToPyDM(link, "", pageUrl, null, kind)) ? null : NOT_REACHABLE);
  const onVideoPage = info.mediaType || info.menuItemId === "pydm-page-video";

  // The button found the post this video belongs to (Facebook, Instagram, X, ...).
  if (info.videoPageUrl && /^https?:/i.test(info.videoPageUrl)) return send(info.videoPageUrl);
  // A YouTube video link (e.g. a thumbnail), or the video playing on a watch page.
  const youtube = [info.linkUrl, onVideoPage ? pageUrl : null].find((u) => u && isYouTubeVideo(u));
  if (youtube) return send(youtube);
  // A link to a single post on a video site (right-clicking a Reel, a tweet, ...).
  if (info.linkUrl && PYDM_SITES.isPostUrl(info.linkUrl)) return send(info.linkUrl);
  // A video on a video site's post page.
  if (onVideoPage && PYDM_SITES.isPostUrl(pageUrl)) return send(pageUrl);
  if (url && /^https?:/i.test(url)) return send(url, "");
  if (tab?.id >= 0 && onVideoPage) {
    if (PYDM_SITES.isVideoSite(pageUrl)) {
      const fb = /(^|\.)facebook\.com$/i.test(new URL(pageUrl).hostname) ? await recentFacebookVideo(tab.id) : null;
      if (fb) return send(fb);  // the video that just played in this tab
      return "Couldn't tell which post this video belongs to. Open the post (click its date or time), then try again.";
    }
    // Streaming players show a blob: URL; send the playlist we saw on this tab instead.
    const media = await bestMediaForTab(tab.id);
    if (media) return (await sendMedia(tab.id, media.url, media.kind)) ? null : NOT_REACHABLE;
    if (isYouTube(pageUrl)) return "Open the video's own page (youtube.com/watch?v=…), then try again.";
    // Nothing seen yet: let yt-dlp look for the video on the page itself.
    if (/^https?:/i.test(pageUrl)) return send(pageUrl);
    return "No downloadable video found on this page yet. Play the video for a few seconds, then try again.";
  }
  return "Nothing to download here: this item has no web address PyDM can fetch.";
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const problem = await downloadFromPage(info, tab);
  if (problem) notify(problem);
});

// The download button content.js puts on videos.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.action !== "download-video" || !sender.tab) return false;
  const info = { mediaType: "video", srcUrl: msg.srcUrl, pageUrl: msg.pageUrl || sender.url,
    videoPageUrl: msg.videoPageUrl };
  downloadFromPage(info, sender.tab).then((problem) => sendResponse({ ok: !problem, message: problem }));
  return true; // async response
});
