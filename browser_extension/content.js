// Puts a "Download" button at the top-right of a video while the mouse is over it.
// Clicking hands the video to background.js, which works out what to send
// (YouTube page, direct file, or the stream detected on this tab).
(() => {
  if (window.__pydmButton) return; // injected twice (e.g. extension reloaded)
  window.__pydmButton = true;

  const MIN_W = 200, MIN_H = 112;    // ignore thumbnails, ads, icons
  const IDLE_HIDE_MS = 2500;         // get out of the way while watching

  let enabled = true;
  let current = null;                // the video the button is on
  let hideTimer = 0, idleTimer = 0, frame = 0;
  let busy = false;

  // --- the button, in a shadow root so page CSS can't touch it ---
  const host = document.createElement("pydm-video-button");
  host.style.cssText = "all: initial; position: fixed; z-index: 2147483647; top: 0; right: 0; display: none;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      .wrap { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
      button {
        display: flex; align-items: center; gap: 6px; cursor: pointer;
        font: 600 13px/1 system-ui, "Segoe UI", sans-serif; color: #fff;
        background: #1565c0; border: 1px solid #ffffff55; border-radius: 6px;
        padding: 7px 11px 7px 9px; box-shadow: 0 2px 8px #0006; opacity: .92;
      }
      button:hover { background: #1976d2; opacity: 1; }
      button:disabled { cursor: default; }
      button.ok { background: #2e7d32; }
      button.bad { background: #c62828; }
      svg { width: 16px; height: 16px; flex: none; }
      .msg {
        max-width: 260px; font: 12px/1.35 system-ui, "Segoe UI", sans-serif; color: #fff;
        background: #000c; border-radius: 6px; padding: 6px 9px; display: none;
      }
    </style>
    <div class="wrap">
      <button title="Download this video with PyDM">
        <svg viewBox="0 0 16 16" fill="#fff"><path d="M6.5 2h3v6h2.7L8 12.3 3.8 8h2.7z"/><rect x="3" y="13" width="10" height="1.4"/></svg>
        <span>Download</span>
      </button>
      <div class="msg"></div>
    </div>`;
  const button = root.querySelector("button");
  const label = root.querySelector("span");
  const msg = root.querySelector(".msg");

  // Don't let clicks reach the player underneath (in fullscreen the button sits inside it).
  for (const type of ["click", "mousedown", "mouseup", "pointerdown", "pointerup", "dblclick"]) {
    host.addEventListener(type, (e) => e.stopPropagation());
  }

  // Fullscreen players and modal viewers (<dialog>, popovers) are drawn in the
  // browser's "top layer", above any z-index: the button must go inside them.
  function topLayerParent() {
    if (document.fullscreenElement) return document.fullscreenElement;
    const layer = current && current.closest("dialog[open]");
    if (layer) return layer;
    try {
      const pop = current && current.closest("[popover]");
      if (pop && pop.matches(":popover-open")) return pop;
    } catch {}
    return document.documentElement;
  }

  function attach() {
    const parent = topLayerParent();
    if (host.parentNode !== parent) parent.appendChild(host);
  }

  // On Facebook, Instagram, X, TikTok, Reddit... the page may be a feed with many
  // videos: find the link of the post this video belongs to (its date/permalink
  // link), for yt-dlp. Nearest first; stop before reaching a neighbouring post.
  // The containers from the video up to its post (stopping before a container
  // that also holds another post's video).
  // The element that is "the post" on sites that mark their posts: Facebook's feed
  // units (aria-posinset), Instagram's <article>, role="article" elsewhere.
  const POST_SELECTOR = '[aria-posinset], [role="article"], article, [data-pagelet^="FeedUnit"]';

  function postContainers(video) {
    const out = [];
    let el = video.parentElement;
    for (let depth = 0; el && el !== document.body && depth < 30; depth++, el = el.parentElement) {
      const videos = el.querySelectorAll("video");
      if (videos.length > 1 && [...videos].some((v) => v !== video && v.getBoundingClientRect().width >= MIN_W
                                                  && !v.contains(video) && !el.matches(POST_SELECTOR))) {
        break;  // reached a container holding other posts' videos
      }
      out.push(el);
      if (el.matches(POST_SELECTOR)) break;  // the post itself: everything we need is inside
    }
    const post = video.closest(POST_SELECTOR);
    if (post && !out.includes(post)) out.push(post);  // e.g. a player that holds a second (background) video
    return out;
  }

  function findPostLink(containers) {
    for (const el of containers) {
      for (const a of el.querySelectorAll("a[href]")) {
        if (PYDM_SITES.isPostUrl(a.href)) return a.href;
      }
    }
    return null;
  }

  async function postUrlFor(video) {
    if (typeof PYDM_SITES === "undefined" || !PYDM_SITES.isVideoSite(location.href)) return null;
    const containers = postContainers(video);
    let found = findPostLink(containers);
    if (!found && containers.length) {
      // Facebook fills in a post's date/permalink link only when the mouse goes over
      // it: do that for the post's links, then look again.
      const post = containers[containers.length - 1];
      const links = [...post.querySelectorAll('a[href="#"], a[role="link"], a:not([href]), [role="link"]')].slice(0, 40);
      for (const a of links) {
        for (const type of ["mouseover", "mouseenter", "focusin", "focus"]) {
          a.dispatchEvent(new MouseEvent(type, { bubbles: type !== "mouseenter" && type !== "focus", view: window }));
        }
      }
      if (links.length) {
        await new Promise((r) => setTimeout(r, 300));
        found = findPostLink(containers);
      }
    }
    if (found) return found;
    return PYDM_SITES.isPostUrl(location.href) ? location.href : null;
  }

  // YouTube: only a watch/shorts page (or an embed) has a video yt-dlp can fetch;
  // the home page's hover previews don't.
  function allowedHere() {
    if (!onYouTube) return true;
    return (location.pathname === "/watch" && new URLSearchParams(location.search).has("v"))
      || /^\/(shorts|live|embed)\/[\w-]+/.test(location.pathname);
  }

  const onYouTube = /(^|\.)(youtube\.com|youtube-nocookie\.com)$/i.test(location.hostname);

  // On YouTube only the main player counts: hovering a suggestion, a queue/playlist
  // entry or a home-page thumbnail plays a preview in its own <video>, and the
  // button there would download the page's video, not the one previewed.
  function isMainYouTubePlayer(v) {
    if (v.closest("ytd-video-preview, #inline-preview-player, #video-preview, ytd-thumbnail, "
                  + "ytd-moving-thumbnail-renderer, ytd-miniplayer")) return false;
    return !!v.closest("#movie_player, #shorts-player, ytd-reel-video-renderer[is-active]");
  }

  function videoAt(x, y) {
    for (const v of document.querySelectorAll("video")) {
      if (onYouTube && !isMainYouTubePlayer(v)) continue;
      const r = v.getBoundingClientRect();
      if (r.width >= MIN_W && r.height >= MIN_H && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
          && getComputedStyle(v).visibility !== "hidden") {
        return v;
      }
    }
    return null;
  }

  function place() {
    frame = 0;
    if (!current || !current.isConnected) return hide(true);
    const r = current.getBoundingClientRect();
    // Anchored by its right edge, so it stays put when the label changes length.
    const width = document.documentElement.clientWidth;
    host.style.right = `${Math.max(4, width - Math.min(r.right, width) + 12)}px`;
    host.style.top = `${Math.max(4, r.top) + 12}px`;
  }

  function show(video) {
    clearTimeout(hideTimer);
    if (current !== video) resetButton();
    current = video;
    attach();
    host.style.display = "block";
    if (!frame) frame = requestAnimationFrame(place);
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => !busy && hide(true), IDLE_HIDE_MS);
  }

  function hide(now = false) {
    clearTimeout(hideTimer);
    const go = () => {
      if (busy) return;
      host.style.display = "none";
      current = null;
    };
    if (now) go(); else hideTimer = setTimeout(go, 400);
  }

  function resetButton() {
    button.disabled = false;
    button.className = "";
    label.textContent = "Download";
    msg.style.display = "none";
  }

  function onMove(e) {
    if (!enabled || !allowedHere()) return;
    const overButton = e.composedPath().includes(host);
    const video = overButton ? current : videoAt(e.clientX, e.clientY);
    if (video) show(video); else if (current) hide();
  }
  // On window, capture phase: runs before pages that stop these events lower down.
  addEventListener("pointermove", onMove, { capture: true, passive: true });
  addEventListener("mousemove", onMove, { capture: true, passive: true });

  for (const type of ["scroll", "resize"]) {
    addEventListener(type, () => { if (current && !frame) frame = requestAnimationFrame(place); },
      { capture: true, passive: true });
  }
  document.addEventListener("fullscreenchange", () => { attach(); if (current) place(); });

  button.addEventListener("click", async () => {
    if (busy || !current) return;
    busy = true;
    const video = current;  // the mouse may move to another video while we look for the post
    button.disabled = true;
    label.textContent = "Sending…";
    let reply;
    try {
      const videoPageUrl = await postUrlFor(video);
      reply = await chrome.runtime.sendMessage({
        action: "download-video",
        srcUrl: video.currentSrc || video.src || "",
        pageUrl: location.href,
        videoPageUrl,
      });
    } catch {
      reply = { ok: false, message: "PyDM was updated. Reload this page, then try again." };
    }
    button.className = reply?.ok ? "ok" : "bad";
    label.textContent = reply?.ok ? "Sent to PyDM" : "Not sent";
    if (!reply?.ok) {
      msg.textContent = reply?.message || "Couldn't reach PyDM.";
      msg.style.display = "block";
    }
    setTimeout(() => {
      busy = false;
      resetButton();
      hide(true);
    }, reply?.ok ? 1500 : 5000);
  });

  chrome.storage.local.get("videoButton").then((s) => { enabled = s.videoButton ?? true; }).catch(() => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && "videoButton" in changes) {
      enabled = changes.videoButton.newValue ?? true;
      if (!enabled) hide(true);
    }
  });
})();
