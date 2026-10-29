"""The Download button content.js puts on videos, tested in a real (headless) Edge.
chrome.* is mocked; background.js's side is covered by test_extension.mjs.
Needs Playwright (pip install playwright) and Microsoft Edge. From the project root:
    python tests/test_video_button.py [screenshot.png]
"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT_JS = open(os.path.join(ROOT, "browser_extension", "content.js"), encoding="utf-8").read()
SITES_JS = open(os.path.join(ROOT, "browser_extension", "sites.js"), encoding="utf-8").read()

# A Facebook-like feed: two posts, each with a video and its timestamp (permalink) link.
FB_FEED = """<!doctype html><html><body style="margin:0;font-family:sans-serif">
<div role="feed">
 <div role="article" style="padding:10px"><a href="/alice/videos/1111/">3 h</a>
  <div style="position:relative;width:480px;height:270px"><video id="fb1" style="width:480px;height:270px;background:#333"></video>
   <div style="position:absolute;inset:0"></div></div></div>
 <div role="article" style="padding:10px"><span>Bob</span> <a href="https://www.facebook.com/reel/2222">Yesterday</a>
  <div style="position:relative;width:480px;height:270px"><video id="fb2" style="width:480px;height:270px;background:#444"></video>
   <div style="position:absolute;inset:0"></div></div></div>
 <div aria-posinset="4" style="padding:10px"><div><a href="/dave/posts/pfbid0XYZ987">5 h</a></div>
  <div><div style="position:relative;width:480px;height:270px">
   <video id="fb4bg" style="position:absolute;inset:0;width:480px;height:270px;filter:blur(20px)"></video>
   <video id="fb4" style="position:absolute;inset:0;width:480px;height:270px;background:#666"></video>
   <div style="position:absolute;inset:0"></div></div></div></div>
 <div role="article" style="padding:10px"><a id="lazy" href="#" role="link">2 d</a>
  <div style="position:relative;width:480px;height:270px"><video id="fb3" style="width:480px;height:270px;background:#555"></video>
   <div style="position:absolute;inset:0"></div></div></div>
</div>
<script>
  // Facebook only sets the real permalink when the link is hovered/focused.
  document.addEventListener("mouseover", (e) => {
    if (e.target.id === "lazy") e.target.href = "/carol/posts/pfbid0ABCdef123";
  });
</script></body></html>"""

# An Instagram Reel page: the "Original audio" link sits right next to the video.
IG_REEL = """<!doctype html><html><body style="margin:0">
<section><div><video id="reel" style="width:360px;height:640px;background:#111"></video>
 <a href="/reels/audio/39475307758750206/">Original audio</a></div></section></body></html>"""

# Instagram-like: a post opened in a modal <dialog> (the browser's top layer, above any z-index).
IG = """<!doctype html><html><body style="margin:0">
<dialog id="d" style="padding:0;border:0"><article><a href="/p/ABC123xyz/">2d</a>
 <video id="ig" style="width:400px;height:500px;background:#222;display:block"></video></article></dialog>
<script>document.getElementById("d").showModal();</script></body></html>"""
SHOT = sys.argv[1] if len(sys.argv) > 1 else None

PAGE = """<!doctype html><html><body style="margin:0;font-family:sans-serif">
<div style="height:120px">header</div>
<div id="movie_player" style="position:relative;width:640px;height:360px;margin-left:40px;background:#222">
  <video id="v" style="width:640px;height:360px"></video>
  <!-- players put controls/overlays above the <video>, so it never gets the mouse -->
  <div id="overlay" style="position:absolute;inset:0"></div>
</div>
<video id="tiny" style="width:120px;height:68px;margin:20px 40px;background:#444"></video>
<!-- YouTube's suggestions/queue: hovering plays a preview in its own player -->
<div id="secondary" style="position:absolute;left:700px;top:120px">
  <div id="inline-preview-player"><video id="preview" style="width:280px;height:158px;background:#555"></video></div>
  <ytd-thumbnail><video id="thumbvid" style="width:280px;height:158px;margin-top:10px;background:#666"></video></ytd-thumbnail>
</div>
<div style="height:2000px"></div>
<script>
  window.overlayClicks = 0;
  document.getElementById("overlay").addEventListener("click", () => window.overlayClicks++);
</script>
</body></html>"""

MOCK = """
window.__sent = []; window.__reply = { ok: true };
Object.defineProperty(window, "chrome", { value: {
  runtime: { sendMessage: async (m) => { window.__sent.push(m); return window.__reply; } },
  storage: { local: { get: async () => ({}) }, onChanged: { addListener: (f) => { window.__onChanged = f; } } },
}});
// Open the button's shadow root so the test can read its label.
const attach = Element.prototype.attachShadow;
Element.prototype.attachShadow = function (o) { return attach.call(this, { ...o, mode: "open" }); };
"""

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1000, "height": 700})
    page.route("https://www.youtube.com/**", lambda r: r.fulfill(body=PAGE, content_type="text/html"))
    page.route("https://videos.example/**", lambda r: r.fulfill(body=PAGE, content_type="text/html"))
    page.route("https://www.facebook.com/**", lambda r: r.fulfill(body=FB_FEED, content_type="text/html"))
    page.route("https://www.instagram.com/reels/**", lambda r: r.fulfill(body=IG_REEL, content_type="text/html"))
    page.route("https://www.instagram.com/", lambda r: r.fulfill(body=IG, content_type="text/html"))
    page.add_init_script(MOCK)

    def load(url):
        page.goto(url)
        page.add_script_tag(content=SITES_JS)
        page.add_script_tag(content=CONTENT_JS)
        page.mouse.move(5, 5)

    def button():
        return page.evaluate("""() => {
            const h = document.querySelector("pydm-video-button");
            if (!h || h.style.display === "none") return null;
            const r = h.getBoundingClientRect(), s = h.shadowRoot;
            return { x: r.left, y: r.top, w: r.width, h: r.height, label: s.querySelector("span").textContent,
                     msg: s.querySelector(".msg").style.display === "block" ? s.querySelector(".msg").textContent : "",
                     parent: h.parentElement.id || h.parentElement.tagName };
        }""")

    def video_rect(id_="v"):
        return page.evaluate(f"(() => {{ const r = document.getElementById('{id_}').getBoundingClientRect();"
                             f" return {{x: r.left, y: r.top, r: r.right, b: r.bottom}}; }})()")

    load("https://www.youtube.com/watch?v=abc123")
    check("hidden until the mouse is over a video", button() is None)
    v = video_rect()
    page.mouse.move(v["x"] + 200, v["y"] + 200)   # lands on the overlay, not the <video>
    page.wait_for_timeout(100)
    b = button()
    check("appears when hovering the video (through the player's overlay)", b is not None, str(b))
    if b:
        check("sits at the video's top-right corner", abs(b["x"] + b["w"] - (v["r"] - 12)) < 2 and b["y"] == v["y"] + 12,
              f"button right/top {b['x'] + b['w']:.0f},{b['y']:.0f} video right/top {v['r']:.0f},{v['y']:.0f}")
        if SHOT:
            page.screenshot(path=SHOT, clip={"x": v["x"] - 10, "y": v["y"] - 10, "width": 660, "height": 120})
        page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        page.wait_for_timeout(150)
        sent = page.evaluate("window.__sent")
        check("click sends the page to PyDM", sent and sent[-1]["action"] == "download-video"
              and sent[-1]["pageUrl"] == "https://www.youtube.com/watch?v=abc123", str(sent))
        check("click doesn't reach the player", page.evaluate("window.overlayClicks") == 0)
        after = button() or {}
        check("shows it was sent, right edge unmoved", after.get("label") == "Sent to PyDM"
              and abs(after["x"] + after["w"] - (b["x"] + b["w"])) < 2, str(after))
        page.wait_for_timeout(1700)
        check("goes away after sending", button() is None)

    page.mouse.move(v["x"] + 300, v["y"] + 150)
    page.wait_for_timeout(100)
    check("comes back on the next hover", button() is not None)
    page.mouse.move(900, 650)
    page.wait_for_timeout(600)
    check("hides when the mouse leaves the video", button() is None)
    page.mouse.move(v["x"] + 300, v["y"] + 150)
    page.wait_for_timeout(2800)
    check("hides when the mouse rests (doesn't block watching)", button() is None)

    t = video_rect("tiny")
    page.mouse.move(t["x"] + 20, t["y"] + 20)
    page.wait_for_timeout(100)
    check("no button on small videos", button() is None)

    page.mouse.move(v["x"] + 200, v["y"] + 200)
    page.mouse.wheel(0, 80)
    page.wait_for_timeout(200)
    page.mouse.move(v["x"] + 210, v["y"] + 120)
    page.wait_for_timeout(100)
    v2, b = video_rect(), button()
    check("follows the video when the page scrolls", b is not None and abs(b["y"] - (max(4, v2["y"]) + 12)) < 2,
          f"{b and b['y']} vs video top {v2['y']}")

    page.evaluate("window.__reply = { ok: false, message: \"Couldn't reach PyDM.\" }")
    b = button()
    page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
    page.wait_for_timeout(150)
    b = button()
    check("failure is explained next to the button", b and b["label"] == "Not sent" and "reach PyDM" in b["msg"], str(b))
    page.wait_for_timeout(5200)

    page.evaluate("document.getElementById('movie_player').requestFullscreen()")
    page.wait_for_timeout(300)
    page.mouse.move(500, 300)
    page.wait_for_timeout(100)
    b = button()
    check("works in fullscreen (moves inside the fullscreen player)", b is not None and b["parent"] == "movie_player", str(b))
    page.evaluate("document.exitFullscreen()")
    page.wait_for_timeout(300)

    for vid in ("preview", "thumbvid"):
        r = video_rect(vid)
        page.mouse.move(r["x"] + 100, r["y"] + 80)
        page.wait_for_timeout(600)  # the main player's button lingers 400 ms after leaving it
        check(f"YouTube watch page: no button on a suggestion's preview ({vid})", button() is None, str(button()))
    page.mouse.move(v["x"] + 200, v["y"] + 200)
    page.wait_for_timeout(100)
    check("…but the main player still has it", button() is not None)

    load("https://www.youtube.com/")
    v = video_rect()
    page.mouse.move(v["x"] + 200, v["y"] + 200)
    page.wait_for_timeout(100)
    check("not on YouTube's home page previews", button() is None)

    load("https://videos.example/watch/1")
    page.evaluate("window.__onChanged({ videoButton: { newValue: false } }, 'local')")
    page.mouse.move(v["x"] + 200, v["y"] + 200)
    page.wait_for_timeout(100)
    check("turning the setting off hides it", button() is None)
    page.evaluate("window.__onChanged({ videoButton: { newValue: true } }, 'local')")
    page.mouse.move(v["x"] + 210, v["y"] + 210)
    page.wait_for_timeout(100)
    check("other sites get the button too", button() is not None)
    r = video_rect("preview")
    page.mouse.move(r["x"] + 100, r["y"] + 80)
    page.wait_for_timeout(100)
    check("other sites: any big video gets it (the YouTube rule doesn't apply)", button() is not None)
    # Facebook feed: each video's button sends its own post
    page.evaluate("window.__reply = { ok: true }")
    load("https://www.facebook.com/")
    for vid, expected in (("fb1", "https://www.facebook.com/alice/videos/1111/"),
                          ("fb2", "https://www.facebook.com/reel/2222"),
                          ("fb3", "https://www.facebook.com/carol/posts/pfbid0ABCdef123"),  # filled on hover
                          ("fb4", "https://www.facebook.com/dave/posts/pfbid0XYZ987")):  # blurred 2nd video
        page.mouse.move(5, 5)
        page.wait_for_timeout(600)
        page.evaluate(f"document.getElementById('{vid}').scrollIntoView()")
        r = video_rect(vid)
        page.mouse.move(r["x"] + 200, r["y"] + 150)
        page.wait_for_timeout(150)
        b = button()
        if b:
            page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
            page.wait_for_timeout(600)
        sent = page.evaluate("window.__sent")
        check(f"Facebook feed: {vid} sends its own post", b is not None and sent
              and sent[-1].get("videoPageUrl") == expected, str(sent[-1] if sent else None))
        page.wait_for_timeout(1700)

    # Instagram Reel: the "Original audio" link must not be taken for the video
    load("https://www.instagram.com/reels/C2oBP1RtLDO/")
    r = video_rect("reel")
    page.mouse.move(r["x"] + 150, r["y"] + 300)
    page.wait_for_timeout(150)
    b = button()
    if b:
        page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        page.wait_for_timeout(600)
    sent = page.evaluate("window.__sent")
    check("Instagram Reel: sends the Reel, not its 'Original audio' page",
          sent and sent[-1].get("videoPageUrl") == "https://www.instagram.com/reels/C2oBP1RtLDO/",
          str(sent[-1] if sent else None))

    # Instagram post in a modal dialog: the button goes inside the top layer
    load("https://www.instagram.com/")
    r = video_rect("ig")
    page.mouse.move(r["x"] + 200, r["y"] + 250)
    page.wait_for_timeout(150)
    b = button()
    check("Instagram viewer (modal dialog): button appears inside it", b is not None and b["parent"] == "d", str(b))
    if b:
        page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
        page.wait_for_timeout(600)
    sent = page.evaluate("window.__sent")
    check("…and sends the post", sent and sent[-1].get("videoPageUrl") == "https://www.instagram.com/p/ABC123xyz/",
          str(sent[-1] if sent else None))
    if SHOT:
        page.screenshot(path=SHOT.replace(".png", "-instagram.png"))
    browser.close()

print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
