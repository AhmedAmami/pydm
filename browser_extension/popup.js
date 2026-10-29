const $ = (id) => document.getElementById(id);

async function load() {
  // background.js stores the defaults on install.
  const s = await chrome.storage.local.get(["enabled", "minSizeKB", "extensions", "videoButton"]);
  $("enabled").checked = s.enabled ?? true;
  $("videoButton").checked = s.videoButton ?? true;
  $("minSizeKB").value = s.minSizeKB ?? 0;
  $("extensions").value = s.extensions ?? "";
}

function save() {
  chrome.storage.local.set({
    enabled: $("enabled").checked,
    videoButton: $("videoButton").checked,
    minSizeKB: Math.max(0, parseInt($("minSizeKB").value, 10) || 0),
    extensions: $("extensions").value.trim(),
  });
}

async function test() {
  const status = $("status");
  status.className = "";
  status.textContent = "Checking…";
  try {
    const reply = await chrome.runtime.sendNativeMessage("com.pydm.host", { type: "ping" });
    status.className = "ok";
    status.textContent = `Connected (v${reply.version})` + (reply.gui_running ? "" : ", app not open");
  } catch (e) {
    status.className = "bad";
    status.textContent = "Host not found. Run install_browser.bat";
  }
}

// --- videos detected on the current tab (collected by background.js) ---

function visibleMedia(items) {
  const hidden = new Set(items.flatMap((i) => i.children || []));
  return items.filter((i) => !hidden.has(i.url));
}

function describe(item) {
  const path = decodeURIComponent(new URL(item.url).pathname);
  const name = path.split("/").filter(Boolean).pop() || new URL(item.url).host;
  if (item.kind === "ytdl") return { tag: item.tag || "VIDEO", text: item.title || "This video" };
  const tag = item.kind === "file" ? (name.split(".").pop() || "file").toUpperCase().slice(0, 4) : item.kind.toUpperCase();
  const size = item.size ? ` · ${(item.size / 1048576).toFixed(1)} MB` : "";
  const label = item.children ? "Video (all qualities)" : name;
  return { tag, text: label + size };
}

async function loadMedia() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const box = $("media");
  box.textContent = "";
  if (!tab) return;
  const key = `media:${tab.id}`;
  let items = visibleMedia((await chrome.storage.session.get(key))[key] || []);
  let host = "";
  try { host = new URL(tab.url).hostname; } catch {}
  const youtube = /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$/i.test(host);
  const videoSite = PYDM_SITES.isVideoSite(tab.url || "");
  let note = "None found yet. Start playing the video, then open this again.";
  if (youtube) {
    // On YouTube the sniffed requests are useless; offer the page's video instead.
    const u = new URL(tab.url);
    const isVideo = (u.pathname === "/watch" && u.searchParams.has("v")) || /^\/(shorts|live)\/[\w-]+/.test(u.pathname);
    items = isVideo ? [{ url: tab.url, kind: "ytdl", title: tab.title.replace(/ - YouTube$/, ""), tag: "YT" }] : [];
    note = "Open a video, then click Download here. Or right-click any video thumbnail → Download with PyDM.";
  } else if (videoSite) {
    // Facebook, Instagram, X, ...: their pieces aren't useful either; yt-dlp takes the post link.
    items = PYDM_SITES.isPostUrl(tab.url) ? [{ url: tab.url, kind: "ytdl", title: tab.title || "Video on this page" }] : [];
    note = "In a feed, use the Download button on the video itself, or open the post (click its date or time).";
  } else if (!items.length && /^https?:/.test(tab.url || "")) {
    // Nothing detected: yt-dlp can still look for a video in the page.
    items = [{ url: tab.url, kind: "ytdl", title: "Find the video on this page", tag: "PAGE" }];
    note = "";
  }
  $("media-note").textContent = items.length ? "" : note;
  for (const item of items) {
    const { tag, text } = describe(item);
    const row = document.createElement("div");
    row.className = "item";
    row.title = item.url;
    const tagEl = document.createElement("span");
    tagEl.className = "tag";
    tagEl.textContent = tag;
    const nameEl = document.createElement("span");
    nameEl.className = "name";
    nameEl.textContent = text;
    const button = document.createElement("button");
    button.textContent = "Download";
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "Sending…";
      const reply = await chrome.runtime.sendMessage({ action: "download-media", tabId: tab.id, url: item.url, kind: item.kind });
      button.textContent = reply?.ok ? "Sent ✓" : "Failed";
      if (!reply?.ok) {
        $("media-note").textContent = "Couldn't reach PyDM. Open Settings below and click Test connection.";
        button.disabled = false;
      }
    });
    row.append(tagEl, nameEl, button);
    box.append(row);
  }
}

for (const id of ["enabled", "videoButton", "minSizeKB", "extensions"]) $(id).addEventListener("change", save);
$("test").addEventListener("click", test);
load();
loadMedia();
