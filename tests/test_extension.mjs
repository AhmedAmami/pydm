// Runs browser_extension/background.js against mocked chrome.* APIs and simulates
// the events a browser would fire. Run from the project root:
//   node tests/test_extension.mjs
import fs from "node:fs";
import vm from "node:vm";

let failures = 0;
function check(name, cond, detail = "") {
  console.log(`[${cond ? "PASS" : "FAIL"}] ${name} ${detail}`);
  if (!cond) failures++;
}
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 5)); };

function event() {
  const listeners = [];
  return { addListener: (f) => listeners.push(f), fire: (...a) => listeners.map((f) => f(...a)) };
}

function store() {
  const data = {};
  return {
    data,
    async get(keys) {
      if (keys == null) return { ...data };
      const list = Array.isArray(keys) ? keys : [keys];
      return Object.fromEntries(list.filter((k) => k in data).map((k) => [k, data[k]]));
    },
    async set(obj) { Object.assign(data, structuredClone(obj)); },
    async remove(k) { for (const key of [].concat(k)) delete data[key]; },
  };
}

const sent = [], cancelled = [], erased = [], badges = {}, menus = [], notes = [];
let nativeOk = true;
const tabs = { 5: { id: 5, url: "https://videos.example/watch/42", title: "Big Buck Bunny" } };
const MASTER = "https://cdn.example/v/master.m3u8";
const chrome = {
  storage: { local: store(), session: store() },
  downloads: {
    onDeterminingFilename: event(),
    cancel: async (id) => cancelled.push(id),
    erase: async (q) => erased.push(q.id),
  },
  webRequest: { onHeadersReceived: event(), onBeforeRequest: event() },
  tabs: { onUpdated: event(), onRemoved: event(), get: async (id) => tabs[id] },
  runtime: {
    onInstalled: event(), onMessage: event(),
    sendNativeMessage: async (host, msg) => {
      if (!nativeOk) throw new Error("Specified native messaging host not found.");
      sent.push({ host, msg });
      return { ok: true };
    },
  },
  cookies: { getAll: async ({ url }) => [{ name: "sid", value: "abc" }, { name: "host", value: new URL(url).host }] },
  contextMenus: {
    onClicked: event(), removeAll: async () => menus.splice(0), create: (m) => menus.push(m),
  },
  notifications: { create: async (opts) => { notes.push(opts.message); return "n"; } },
  action: {
    setBadgeText: async ({ tabId, text }) => { badges[tabId] = text; },
    setBadgeBackgroundColor: async () => {},
  },
};
const fetch = async (url) => ({
  text: async () => url === MASTER
    ? "#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"a\",URI=\"audio/en.m3u8\"\n"
      + "#EXT-X-STREAM-INF:BANDWIDTH=1\nlow/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2\nhigh/index.m3u8\n"
    : "#EXTM3U\n#EXTINF:4,\nseg0.ts\n#EXT-X-ENDLIST\n",
});

const context = vm.createContext({ chrome, fetch, URL, console, setTimeout, atob, navigator: { userAgent: "TestUA/1.0" } });
// The service worker loads sites.js with importScripts().
context.importScripts = (file) => vm.runInContext(fs.readFileSync(`browser_extension/${file}`, "utf8"), context);
vm.runInContext(fs.readFileSync("browser_extension/background.js", "utf8"), context);

// Install: defaults + context menu
await Promise.all(chrome.runtime.onInstalled.fire({ reason: "install" }));
check("defaults stored on install", chrome.storage.local.data.enabled === true
  && chrome.storage.local.data.extensions.includes("zip"));
check("context menus created", menus.length === 2 && menus[0].contexts.includes("video")
  && menus[1].documentUrlPatterns.some((p) => p.includes("youtube.com/watch"))
  && menus[1].documentUrlPatterns.includes("*://*.facebook.com/*")
  && menus[1].documentUrlPatterns.includes("*://*.instagram.com/*"));

// Download capture
async function download(item) {
  let suggested = false;
  const keepAlive = chrome.downloads.onDeterminingFilename.fire(item, () => { suggested = true; })[0];
  await flush();
  return { suggested, keepAlive };
}
let r = await download({ id: 1, url: "https://files.example/get?id=9", finalUrl: "https://files.example/a/setup.zip",
  filename: "C:\\Users\\me\\Downloads\\setup.zip", referrer: "https://files.example/page", totalBytes: 5e6 });
const zipMsg = sent.at(-1)?.msg;
check("zip download handed to PyDM", zipMsg?.url === "https://files.example/a/setup.zip"
  && zipMsg.filename === "setup.zip" && zipMsg.referrer === "https://files.example/page"
  && zipMsg.cookies === "sid=abc; host=files.example" && zipMsg.userAgent === "TestUA/1.0"
  && zipMsg.fileSize === 5e6 && zipMsg.kind === "", JSON.stringify(zipMsg));
check("browser download cancelled and erased", cancelled.includes(1) && erased.includes(1));
check("suggest() called asynchronously", r.suggested && r.keepAlive === true);

const before = sent.length;
r = await download({ id: 2, url: "https://site.example/page.html", filename: "page.html", totalBytes: 2000 });
check("non-listed type left to the browser", sent.length === before && !cancelled.includes(2) && r.suggested);

r = await download({ id: 3, url: "blob:https://site.example/xyz", filename: "clip.mp4" });
check("blob: URLs left to the browser", sent.length === before && !cancelled.includes(3));

chrome.storage.local.data.minSizeKB = 1024;
await download({ id: 4, url: "https://files.example/tiny.zip", filename: "tiny.zip", totalBytes: 1000 });
check("files under the minimum size are skipped", sent.length === before && !cancelled.includes(4));
chrome.storage.local.data.minSizeKB = 0;

nativeOk = false;
r = await download({ id: 5, url: "https://files.example/b.zip", filename: "b.zip", totalBytes: 5e6 });
check("if PyDM is unreachable the browser keeps the download", !cancelled.includes(5) && r.suggested);
nativeOk = true;

chrome.storage.local.data.enabled = false;
await download({ id: 6, url: "https://files.example/c.zip", filename: "c.zip" });
check("capture switch off -> nothing sent", sent.length === before && !cancelled.includes(6));
chrome.storage.local.data.enabled = true;

// Video detection
function response(tabId, url, type, headers) {
  chrome.webRequest.onHeadersReceived.fire({
    tabId, url, type, statusCode: 200,
    responseHeaders: Object.entries(headers).map(([name, value]) => ({ name, value })),
  });
}
response(5, MASTER, "xmlhttprequest", { "Content-Type": "application/vnd.apple.mpegurl" });
await flush();
response(5, "https://cdn.example/v/high/index.m3u8", "xmlhttprequest", {});
response(5, "https://cdn.example/v/audio/en.m3u8", "xmlhttprequest", {});
response(5, MASTER, "xmlhttprequest", {});  // repeat request
response(5, "https://cdn.example/v/high/seg0.ts", "xmlhttprequest", { "Content-Type": "video/mp2t" });
await flush();
let items = chrome.storage.session.data["media:5"] || [];
check("playlists recorded once each", items.length === 3, items.map((i) => i.url).join(", "));
check("master's quality/audio playlists hidden (badge 1)", badges[5] === "1", `badge=${badges[5]}`);

response(5, "https://dash.example/x/manifest", "xmlhttprequest", { "content-type": "application/dash+xml" });
response(5, "https://media.example/movie.mp4", "media", { "content-type": "video/mp4", "content-range": "bytes 0-1023/52428800" });
response(5, "https://media.example/click.mp4", "media", { "content-type": "video/mp4", "content-length": "20000" });
response(-1, "https://bg.example/x.m3u8", "xmlhttprequest", {});
await flush();
items = chrome.storage.session.data["media:5"];
check("DASH (by content type) and big MP4 detected; tiny MP4 and non-tab requests ignored",
  badges[5] === "3" && items.some((i) => i.kind === "dash") && items.some((i) => i.kind === "file" && i.size === 52428800)
  && !items.some((i) => i.url.includes("click")) && !chrome.storage.session.data["media:-1"], `badge=${badges[5]}`);

// Popup "Download" button -> background
let reply;
chrome.runtime.onMessage.fire({ action: "download-media", tabId: 5, url: MASTER, kind: "hls" }, {}, (x) => { reply = x; });
await flush();
const vid = sent.at(-1).msg;
check("popup download sends stream with page title + referrer", reply?.ok && vid.url === MASTER && vid.kind === "hls"
  && vid.title === "Big Buck Bunny" && vid.referrer === "https://videos.example/watch/42", JSON.stringify(vid));

// Right-click a <video> whose src is a blob: -> best detected stream
chrome.contextMenus.onClicked.fire({ mediaType: "video", srcUrl: "blob:https://videos.example/1", pageUrl: tabs[5].url }, tabs[5]);
await flush();
check("blob video right-click sends the master playlist", sent.at(-1).msg.url === MASTER && sent.at(-1).msg.kind === "hls");

chrome.contextMenus.onClicked.fire({ linkUrl: "https://files.example/doc.pdf", pageUrl: tabs[5].url }, tabs[5]);
await flush();
check("link right-click sends the link", sent.at(-1).msg.url === "https://files.example/doc.pdf");

// The Download button content.js shows on videos
async function videoButton(msg, tab) {
  let reply;
  chrome.runtime.onMessage.fire({ action: "download-video", ...msg }, { tab, url: msg.pageUrl }, (x) => { reply = x; });
  await flush();
  return reply;
}
let br = await videoButton({ srcUrl: "blob:https://videos.example/9", pageUrl: tabs[5].url }, tabs[5]);
check("video button on a streaming site sends the detected playlist", br?.ok && sent.at(-1).msg.url === MASTER
  && sent.at(-1).msg.kind === "hls", JSON.stringify(br));
br = await videoButton({ srcUrl: "blob:https://www.youtube.com/3", pageUrl: "https://www.youtube.com/watch?v=zzz" },
  { id: 11, url: "https://www.youtube.com/watch?v=zzz", title: "YT" });
check("video button on YouTube sends the watch page as ytdl", br?.ok && sent.at(-1).msg.url.endsWith("v=zzz")
  && sent.at(-1).msg.kind === "ytdl");
br = await videoButton({ srcUrl: "blob:https://empty.example/1", pageUrl: "https://empty.example/" },
  { id: 12, url: "https://empty.example/", title: "E" });
check("video button with nothing detected: yt-dlp looks for it in the page", br?.ok
  && sent.at(-1).msg.url === "https://empty.example/" && sent.at(-1).msg.kind === "ytdl", JSON.stringify(sent.at(-1).msg));

// Facebook / Instagram: the button sends the post the video belongs to
const fbFeed = { id: 13, url: "https://www.facebook.com/", title: "Facebook" };
br = await videoButton({ srcUrl: "blob:https://www.facebook.com/a", pageUrl: fbFeed.url,
  videoPageUrl: "https://www.facebook.com/someone/videos/987654/" }, fbFeed);
check("Facebook feed: the video's post goes to yt-dlp, with the site's cookies", br?.ok
  && sent.at(-1).msg.url === "https://www.facebook.com/someone/videos/987654/" && sent.at(-1).msg.kind === "ytdl"
  && sent.at(-1).msg.cookies.includes("host=www.facebook.com"), JSON.stringify(sent.at(-1).msg));
// Facebook feed, post link not found: the video that just played in the tab (its id is in the fbcdn URL)
const efg = Buffer.from(JSON.stringify({ vencode_tag: "dash_h264_720p", video_id: 1234567890123, xpv_asset_id: 1234567890123 }))
  .toString("base64").replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
chrome.webRequest.onBeforeRequest.fire({ tabId: 13, type: "xmlhttprequest",
  url: `https://video-cdg4-1.xx.fbcdn.net/o1/v/t2/f2/m69/abc.mp4?efg=${efg}&_nc_ht=x&bytestart=0&byteend=999` });
await flush();
br = await videoButton({ srcUrl: "blob:https://www.facebook.com/z", pageUrl: fbFeed.url, videoPageUrl: null }, fbFeed);
check("Facebook feed without a post link: the video that just played (id from fbcdn) goes to yt-dlp", br?.ok
  && sent.at(-1).msg.url === "https://www.facebook.com/watch/?v=1234567890123" && sent.at(-1).msg.kind === "ytdl",
  JSON.stringify(sent.at(-1).msg));
const igFeed = { id: 14, url: "https://www.instagram.com/", title: "Instagram" };
const sentBeforeIg = sent.length;
br = await videoButton({ srcUrl: "blob:https://www.instagram.com/b", pageUrl: igFeed.url, videoPageUrl: null }, igFeed);
check("Instagram feed, post not found: asks to open the post (nothing wrong is sent)", br && !br.ok
  && /Open the post/.test(br.message) && sent.length === sentBeforeIg, JSON.stringify(br));
const igPost = { id: 15, url: "https://www.instagram.com/reel/C2oBP1RtLDO/", title: "Reel" };
br = await videoButton({ srcUrl: "blob:https://www.instagram.com/c", pageUrl: igPost.url }, igPost);
check("Instagram post page: the page goes to yt-dlp", br?.ok && sent.at(-1).msg.url === igPost.url
  && sent.at(-1).msg.kind === "ytdl");
chrome.contextMenus.onClicked.fire({ linkUrl: "https://x.com/someone/status/1234567890", pageUrl: "https://x.com/home" },
  { id: 16, url: "https://x.com/home" });
await flush();
check("right-clicking a link to a post (X) sends it to yt-dlp", sent.at(-1).msg.url.endsWith("/status/1234567890")
  && sent.at(-1).msg.kind === "ytdl");
chrome.contextMenus.onClicked.fire({ menuItemId: "pydm-page-video", pageUrl: "https://www.facebook.com/watch/?v=42" },
  { id: 17, url: "https://www.facebook.com/watch/?v=42" });
await flush();
check("page menu on a Facebook video page", sent.at(-1).msg.url.endsWith("watch/?v=42") && sent.at(-1).msg.kind === "ytdl");

// Feedback when there is nothing to send, or PyDM can't be reached
const yt = { id: 7, url: "https://www.youtube.com/watch?v=abc", title: "YT" };
const sentBefore = sent.length;
chrome.contextMenus.onClicked.fire({ mediaType: "video", srcUrl: "blob:https://www.youtube.com/1", pageUrl: yt.url }, yt);
await flush();
let ytMsg = sent.at(-1).msg;
check("YouTube video right-click sends the watch page as ytdl", sent.length === sentBefore + 1
  && ytMsg.url === yt.url && ytMsg.kind === "ytdl" && ytMsg.title === "", JSON.stringify(ytMsg));
chrome.contextMenus.onClicked.fire({ menuItemId: "pydm-page-video", pageUrl: yt.url }, yt);
await flush();
check("YouTube page menu item sends the video", sent.at(-1).msg.url === yt.url && sent.at(-1).msg.kind === "ytdl");
chrome.contextMenus.onClicked.fire({ linkUrl: "https://www.youtube.com/shorts/xyz123", pageUrl: "https://www.youtube.com/" }, yt);
await flush();
check("YouTube thumbnail link sends ytdl", sent.at(-1).msg.url.endsWith("/shorts/xyz123") && sent.at(-1).msg.kind === "ytdl");
chrome.contextMenus.onClicked.fire({ linkUrl: "https://www.youtube.com/@channel", pageUrl: "https://www.youtube.com/" }, yt);
await flush();
check("non-video YouTube link sent as a plain link", sent.at(-1).msg.url.endsWith("/@channel") && sent.at(-1).msg.kind === "");
const home = { id: 9, url: "https://www.youtube.com/", title: "YouTube" };
const n = sent.length;
chrome.contextMenus.onClicked.fire({ mediaType: "video", srcUrl: "blob:https://www.youtube.com/2", pageUrl: home.url }, home);
await flush();
check("YouTube home-page preview -> 'open the video' notification", sent.length === n && /Open the video/.test(notes.at(-1) || ""), notes.at(-1));
const other = { id: 8, url: "https://news.example/story", title: "News" };
chrome.contextMenus.onClicked.fire({ mediaType: "video", srcUrl: "blob:https://news.example/1", pageUrl: other.url }, other);
await flush();
check("no detected video on another site -> yt-dlp gets the page", sent.length === n + 1
  && sent.at(-1).msg.url === other.url && sent.at(-1).msg.kind === "ytdl");
nativeOk = false;
chrome.contextMenus.onClicked.fire({ linkUrl: "https://files.example/x.zip", pageUrl: other.url }, other);
await flush();
nativeOk = true;
check("unreachable PyDM on right-click -> notification", /Couldn't reach PyDM/.test(notes.at(-1) || ""), notes.at(-1));

// Navigation clears the list; closing the tab drops it
chrome.tabs.onUpdated.fire(5, { status: "loading", url: "https://videos.example/watch/43" });
await flush();
check("navigation clears detected videos", (chrome.storage.session.data["media:5"] || []).length === 0 && badges[5] === "");
response(5, MASTER, "xmlhttprequest", {});
await flush();
chrome.tabs.onRemoved.fire(5);
await flush();
check("closed tab's list removed", !("media:5" in chrome.storage.session.data));

console.log(`\n${failures} failure(s)`);
process.exit(failures ? 1 : 0);
