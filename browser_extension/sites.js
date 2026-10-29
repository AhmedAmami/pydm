// Shared by background.js, content.js and popup.js.
// Video sites whose players stream in small pieces the extension can't simply
// grab: PyDM gives yt-dlp the link of the post/video page instead. For each, the
// pattern a single video's own link matches (so in a feed, the button can find
// the post a video belongs to, e.g. its timestamp link).
var PYDM_SITES = (() => {
  const POSTS = {
    "facebook.com": /\/(videos|reel|reels)\/[\w.-]+|\/watch\/?\?v=\d+|story_fbid=|\/permalink\/|\/posts\/[\w-]+|\/share\/[rv]\//,
    "fb.watch": /^\/[\w-]+/,
    // not /reels/audio/<id>/: the "Original audio" link next to every Reel
    "instagram.com": /\/(p|reel|reels|tv)\/(?!audio\/)[\w-]+/,
    "threads.net": /\/post\/[\w-]+/,
    "threads.com": /\/post\/[\w-]+/,
    "x.com": /\/status\/\d+/,
    "twitter.com": /\/status\/\d+/,
    "tiktok.com": /\/video\/\d+|^\/v\/\d+/,
    "reddit.com": /\/comments\/\w+/,
    "vimeo.com": /^\/(\d+|channels\/[\w-]+\/\d+)/,
    "dailymotion.com": /\/video\/\w+/,
    "twitch.tv": /\/videos\/\d+|\/clip\/[\w-]+/,
    "linkedin.com": /\/posts\/|\/feed\/update\//,
    "pinterest.com": /\/pin\/\d+/,
    "tumblr.com": /\/post\/\d+|^\/[\w-]+\/\d+/,
    "bilibili.com": /\/video\/\w+/,
    "streamable.com": /^\/\w+$/,
    "rumble.com": /^\/v\w+/,
    "odysee.com": /^\/@[^/]+\/[^/]+/,
    "ok.ru": /\/video\/\d+/,
    "vk.com": /video-?\d+_\d+|\/clip-?\d+_\d+/,
    "9gag.com": /\/gag\/\w+/,
    "soundcloud.com": /^\/[\w-]+\/[\w-]+/,
  };

  function siteOf(url) {
    const host = new URL(url).hostname.toLowerCase().replace(/^(www|m|web|mobile|mbasic)\./, "");
    return Object.keys(POSTS).find((d) => host === d || host.endsWith("." + d)) || null;
  }

  return {
    domains() {
      return Object.keys(POSTS);
    },
    // A site PyDM sends to yt-dlp (by page link).
    isVideoSite(url) {
      try { return !!siteOf(url); } catch { return false; }
    },
    // The link of a single post/video on such a site (not a feed or profile page).
    isPostUrl(url) {
      try {
        const site = siteOf(url);
        if (!site) return false;
        const u = new URL(url);
        return POSTS[site].test(u.pathname + u.search);
      } catch {
        return false;
      }
    },
  };
})();
