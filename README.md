# PyDM

A segmented, resumable download manager for Windows, written in Python with a PyQt6 GUI. It also downloads HLS and DASH streaming video, and videos from YouTube, Facebook, Instagram and ~1,750 other sites through yt-dlp.

## Install

Download `PyDM-Setup-<version>.exe` from the **Releases** page and run it. A step-by-step guide for non-technical users is in [RELEASE_NOTES.md](RELEASE_NOTES.md). The installer is unsigned, so Windows SmartScreen asks once (More info → Run anyway). It installs for your user only, so there's no admin prompt, into `%LOCALAPPDATA%\Programs\PyDM`. It adds Start menu and desktop shortcuts, and can optionally start PyDM with Windows. Python isn't needed: ffmpeg, yt-dlp and Deno are bundled.

The installer connects PyDM to Brave, Chrome and Edge. To add the extension, open **Settings → Browser** in PyDM, which shows the steps and the extension folder (`%LOCALAPPDATA%\Programs\PyDM\browser_extension`). Uninstall from Windows Settings → Apps.

The installed app and a copy run from source share the browser connection. Whichever started last receives the browser's downloads.

## Using it

- **Sidebar.** Downloads are grouped into Downloading, Completed and Unfinished, and by type (Videos, Music, Documents, Archives, Programs, Images). Each group shows a count. Type `Ctrl+F` to search by name or link.
- **Adding.** Use **Add URL** (`Ctrl+N`), paste a link anywhere in the window (`Ctrl+V`), drag a link onto the window, or let the browser extension send it. Connections, Referer and Cookies are under **Advanced options** in the Add dialog.
- **Selecting several.** Tick the checkboxes, `Ctrl`+click, `Shift`+click for a range, or drag across rows. The box in the header selects all (`Ctrl+A`).
- **Deleting.** **Delete** (`Del`) asks whether to also delete the downloaded files from disk, and remembers your choice. `Shift+Del` starts with that option ticked. Unfinished downloads always lose their partial data. A file that's open in another program stays on disk, and PyDM tells you so.
- **Speed limit.** Click **Speed limit** in the status bar, or open the tray menu, to choose 256 KB/s – 10 MB/s or Unlimited. Custom values are in Settings → Downloads. The limit covers all downloads together, every connection included, and changes take effect immediately, even mid-download.
- **Copied links.** When you copy a download link in another program (a `.zip`, `.exe`, `.mp4`, `.pdf`…) or a video page link (YouTube, Vimeo, X…), PyDM offers to download it. It offers each link once, and ignores links you copy inside PyDM. You can turn this off in Settings → General.
- **Folders by type.** New downloads go into subfolders of your download folder: `Video`, `Music`, `Documents`, `Compressed`, `Programs` and `Images`. "Audio only" YouTube downloads go to `Music`. If you choose a folder in the Add dialog, that folder is used. You can turn sorting off in Settings → General.
- **Already downloaded?** Adding a link that's already in the list asks first. For a finished download you can **Open file**, for an unfinished one **Resume it**, or you can **Download again**. YouTube links count as the same when they point at the same video, whether `watch?v=`, `youtu.be/` or `shorts/`.
- **Queues and the scheduler.** Queues are listed in the sidebar under QUEUES, and the **Scheduler** button (`Ctrl+J`) edits them.
  - A queue downloads its files in order, one at a time by default.
  - It starts when you click **Start queue**, or at a set time on chosen days, and can stop at another time.
  - When everything has finished it can do nothing, exit PyDM, put the PC to sleep, or shut it down. Sleep and shut down show a 60-second countdown you can cancel.
  - To add a download to a queue, use **When → Add to …** in the Add dialog, or right-click → **Add to queue**.
  - Reorder a queue with right-click → Move up/down (`Ctrl+↑`/`Ctrl+↓`) while viewing it.
  - A queue only counts as finished if everything it started ran to the end. If you pause one of its downloads by hand, it just stops, with no shutdown.
- **Expired links.** Links from file hosts and video sites stop working after a while. When that happens (usually a 403 error), click **Refresh link…** in the details panel (or right-click, or `Ctrl+L`).
  - The dialog can open the page the download came from.
  - Start the download again in the browser, or copy its link. PyDM uses it for the stopped download instead of adding a new one, and continues from where it stopped. You can also paste the link into the dialog.
  - If the new link is for a different file size, PyDM asks before starting over.
  - For HLS/DASH videos, finished segments are kept when the new link offers the same quality. Otherwise that video starts over, so segments of two qualities never get mixed.
- **yt-dlp updates.** YouTube and other video sites break old yt-dlp versions every few weeks. PyDM checks PyPI once a day and downloads a newer yt-dlp into `%LOCALAPPDATA%\PyDM\yt-dlp`. Before using it, PyDM checks its checksums and makes sure it starts, in a separate process. When it's ready, a **Restart PyDM** button appears in the status bar. Downloads pause and continue after the restart. A failed YouTube download also triggers a check. To check by hand or turn this off, use Settings → About. (`pydm/updates.py`)
- **Finished downloads.** A small window in the bottom-right corner offers **Open** and **Open folder**. It doesn't take the focus, and if several downloads finish it updates rather than piling up. It can be turned off in the window itself or in Settings → General. If it's off, the tray notification is used.
- **Virus scan.** Finished downloads are checked with Microsoft Defender (`MpCmdRun.exe`, part of Windows; no admin rights needed).
  - By default only programs and archives are checked (`.exe`, `.msi`, `.zip`, `.rar`, `.apk`, macro documents…); Settings → General can check all files or none.
  - The result appears in the completion window and the details panel.
  - If Defender finds a threat, it removes the file as usual and PyDM marks the download **Threat found**. (`pydm/scan.py`)
- **Connections while downloading.** Change a download's connections from the details panel (**Connections: N**) or right-click → Connections. Raising them starts extra connections that take over part of the remaining work. Lowering them makes connections finish their current chunk and hand the rest back. Nothing is lost.
- **Queues after a restart.** A queue that was running when PyDM closed, the PC restarted or the power went out continues on the next start, unless its stop time passed in between. This can be turned off in Settings → Downloads.
- **If something goes wrong.** PyDM keeps running after an unexpected error. It says so once, and writes the details to `%LOCALAPPDATA%\PyDM\logs\pydm.log`, with hard crashes in `crash.log`. Settings → About → **Open log folder**. Send that file when reporting a problem. Downloads finishing or failing, update checks and virus scans are logged too. (`pydm/log.py`)
- **Keys.** `Space` pauses or resumes the selection, `Enter` opens a finished file, and `Ctrl+E` shows it in its folder. Right-click a download for more.
- **Tray.** Closing the window keeps PyDM in the notification area, so downloads continue. Notifications say when a download finishes (click one to open the folder) or fails. Both can be changed in Settings.
- **Settings.** Light, dark or "match Windows". Default folder, connections, and how many downloads run at once. The browser connection's status, with a **Repair connection** button. **About** shows the bundled component versions.

## Run from source

Double-click `run.bat`. The first run creates `.venv` and installs the dependencies. They are reinstalled automatically whenever `requirements.txt` changes.

Or from a terminal:

```
.venv\Scripts\python -m pydm                          # GUI
.venv\Scripts\python -m pydm.cli URL -o DIR -c 8      # headless; Ctrl+C pauses, rerun to resume
.venv\Scripts\python -m pydm.cli VIDEO.m3u8 -q 720p  # videos: -q best (default), worst, or a height
.venv\Scripts\python -m pydm.cli YOUTUBE_URL -q audio  # YouTube; audio = soundtrack only (.m4a)
```

## Browser integration (Chrome / Edge / Brave)

1. Run `install_browser.bat`. It registers the native messaging host under HKCU, so no admin rights are needed. `install_browser.bat --uninstall` removes it.
2. Open `chrome://extensions`, `edge://extensions` or `brave://extensions` and turn on **Developer mode**. Click **Load unpacked** and choose the `browser_extension` folder.
3. Click the PyDM toolbar icon, then **Test connection**.

Downloads whose file type is on the list are sent to PyDM along with the site's cookies, the referer and the browser's user agent, so downloads that need a login still work. PyDM opens the Add dialog already filled in, and starts itself first if it isn't running. You can also right-click a link or video and choose **Download with PyDM**. The file-type list, the on/off switch and a minimum size are in the toolbar popup.

```
extension --sendNativeMessage--> native_host (python, stdio) --named pipe--> PyDM window
```

The browser's own download is cancelled only after PyDM confirms it received the link. If PyDM is missing or fails, the browser finishes the download normally. Links from blob: and data: URLs, and POST-form downloads, can't be fetched again, so they stay in the browser.

## Video (HLS and DASH)

Paste an `.m3u8` or `.mpd` link into **Add URL**, or let the extension find one. Hovering over a video shows a **Download** button at its top-right (`content.js`); it can be turned off in the popup's Settings. On YouTube only the main player gets it, not the hover previews of suggested or queued videos. Its popup lists the videos on the current page, and the toolbar badge shows how many it found. If the player shows a `blob:` URL, right-clicking the video still works. The Add dialog loads the available qualities, and the page title becomes the file name.

1. **Plan.** The app reads the playlist and picks the variant. A separate audio track is added if there is one. The result is a list of pieces: init sections, media segments and AES keys.
2. **Fetch.** Pieces download in parallel into `<name>.pydm-parts\`. A piece counts as done once its file exists, so resuming skips finished segments, even after a crash.
3. **Merge.** ffmpeg joins the pieces and muxes video and audio into an `.mp4`, or an `.mkv` for WebM/VP9. It doesn't re-encode, so merging is quick. ffmpeg comes from the `imageio-ffmpeg` package unless one is already on your PATH. Without ffmpeg, a single unencrypted stream is simply joined into a `.ts` file.

Supported: HLS master and media playlists, MPEG-TS and fMP4 segments, `EXT-X-BYTERANGE`, separate audio renditions, and AES-128. Also DASH `SegmentTemplate` (`$Number$` and `SegmentTimeline`), `SegmentList` and single-file representations, in MP4 or WebM.

**Not supported:**
- DRM-protected video (Widevine, PlayReady, FairPlay, SAMPLE-AES), as used by Netflix, Disney+, Prime Video and others. PyDM detects it and refuses with an explanation. Getting around DRM is illegal in most countries, and this tool won't do it. Plain AES-128 HLS encryption isn't DRM: any player gets the key, and ffmpeg decrypts it the same way.
- Live streams.
- Multi-period DASH, which is mostly server-inserted ads.

## YouTube and other video sites

YouTube's player doesn't fetch plain HLS/DASH playlists, so YouTube links go to [yt-dlp](https://github.com/yt-dlp/yt-dlp) instead (`pydm/ytdl.py`). So do pages on the ~1,750 other sites yt-dlp knows, such as Vimeo, X/Twitter, Facebook, Reddit, Twitch, Dailymotion and SoundCloud. Paste the page link, and the Add dialog names the site and lists its qualities. Dropbox and Google Drive links, and links ending in a file name like `.zip`, stay normal downloads. Some sites, Vimeo for example, only work when you're signed in. Send those from the browser so your login comes along. It appears in the same list, with progress, pause and resume.

- **From Brave/Chrome/Edge:** hover over the video and click the blue **Download** button at its top-right, or open the PyDM popup and click **Download**. You can also right-click the video. YouTube shows its own menu on the first right-click, so right-click again, or right-click outside the player and choose **Download this YouTube video with PyDM**. Right-clicking a thumbnail anywhere on YouTube also works.
- **In the app:** paste the link into **Add URL**. Qualities go up to 4K, plus **Audio only (m4a)**. The file is named after the video title. H.264/AAC is preferred, so the result plays everywhere. 4K and 1440p exist only as VP9/AV1, and those are saved as `.mkv` if they don't fit in `.mp4`.
- Videos sent from the browser bring your YouTube cookies with them. That handles "Sign in to confirm you're not a bot" and age-restricted videos. The cookies are written to a temporary file for yt-dlp and deleted afterwards.
- yt-dlp uses Deno (installed as the `deno` package) to run YouTube's player code. Without it, some qualities go missing.
- Single videos only. Playlists and live streams are refused.
- YouTube changes often. PyDM keeps yt-dlp up to date by itself (see *yt-dlp updates* above); Settings → About → **Check for updates** forces a check. (`update_ytdlp.bat` updates the copy in `.venv` when running from source.)
- Only download videos you're allowed to save, such as your own uploads or Creative Commons videos. YouTube's terms forbid downloading without permission.

### Facebook, Instagram, X, TikTok and other social sites

These players stream in small pieces the extension can't grab directly, and in a feed the page address isn't the video's. So the extension finds the video's own post link and gives that to yt-dlp, together with your login cookies for the site:
- **The Download button on a video** looks for the post link (the date/time link) in the same post. That works in the Facebook and Instagram feeds and in Instagram's post viewer.
- **Right-clicking** a link to a post (a Reel, a tweet…), or anywhere on a single post's page → *Download the video on this page with PyDM*.
- **The toolbar popup** on a post page.

`browser_extension/sites.js` lists the sites (Facebook, Instagram, Threads, X, TikTok, Reddit, Vimeo, Dailymotion, Twitch, LinkedIn, Pinterest, Tumblr, Bilibili, …) and what their post links look like.

- **Posts with several videos** (Instagram carousels, Facebook albums) list *Video 1 of 3*, *Video 2 of 3*… in the Add dialog's Quality menu.
- **Sites that don't give heights** (Facebook's HD/SD) show *Best quality / HD / SD*.
- **Most of Instagram and some of Facebook need you signed in.** Send those from the browser, not by pasting the link, so your login comes along. If it's missing, PyDM says so.

**Any other site:** if the extension hasn't detected a stream, the button and the popup's *Find the video on this page* hand the page to yt-dlp, which can find videos embedded in many pages. DRM-protected services (Netflix, Disney+, Prime Video…) remain refused.

## How it works

1. **Probe.** The app sends `GET` with `Range: bytes=0-0`. A `206` reply means the server supports ranges, and `Content-Range` gives the total size. This is more reliable than `HEAD`.
2. **Plan.** The file is split into N byte ranges (one per connection). The `.part` file is pre-allocated as a sparse file on NTFS, so writes far into the file don't stall while Windows zero-fills the gap.
3. **Fetch.** Each worker thread requests `Range: bytes=start-end` and writes at that offset.
4. **Work stealing.** When a worker finishes its own range, it takes the back half of the largest remaining range. All connections stay busy until the end, which is where most of the "acceleration" comes from.
5. **Pause/resume.** Segment progress is saved to `<file>.part.json` every 2 seconds and on pause. On resume the app probes again. If the size or ETag changed, it starts over. Otherwise it requests only the missing bytes.
6. **Fallbacks.** If the server doesn't support ranges, the download uses a single connection, and resuming means starting again. If the length is unknown (chunked encoding), it reads until EOF.

Network errors are retried with backoff. A 4xx error other than 429 fails right away.

## Layout

| File | Purpose |
|---|---|
| `pydm/engine.py` | `DownloadTask` (one download) and `DownloadManager` (queue, concurrency limit, persistence). No GUI code. |
| `pydm/streams.py` | `StreamTask`: HLS/DASH parsing, parallel segment download, ffmpeg merge. Same interface as `DownloadTask`. |
| `pydm/ytdl.py` | `YtdlTask`: YouTube via yt-dlp. It reuses `StreamTask`'s controls and naming. |
| `pydm/queues.py` | Queues and the scheduler: order, how many at once, start/stop times, the after-finish action. No Qt. |
| `pydm/updates.py` | Downloads, verifies and activates newer yt-dlp versions (ahead of the bundled one on `sys.path`). |
| `pydm/scan.py` | Scans finished downloads with Microsoft Defender and reads its result. |
| `browser_extension/sites.js` | The video sites sent to yt-dlp, and their post-link patterns (shared by the button, menus and popup). |
| `pydm/log.py` | Log file, and the error handler that keeps PyDM running after an unexpected error. |
| `pydm/theme.py` | Light/dark palettes, the stylesheet, SVG line icons and file-type badges. |
| `pydm/buildcheck.py` | Component versions (Settings → About) and the `--build-check` for packaged builds. |
| `packaging/` | PyInstaller spec, entry scripts and the Inno Setup script. |
| `tools/make_icons.py` | Draws the app icon at each size (`pydm/assets`, `.ico`, extension icons). |
| `pydm/gui.py` | PyQt6 window. It polls `task.snapshot()` on a timer, so Qt is never touched from worker threads. |
| `pydm/cli.py` | Headless front end. |
| `pydm/ipc.py` | Named-pipe messaging to the running window. It also keeps PyDM to a single instance. |
| `pydm/native_host.py` | The program the browser starts. It speaks length-prefixed JSON over stdio. |
| `pydm/install_browser.py` | Registers the host with Chrome and Edge. |
| `browser_extension/` | Manifest V3 extension. The `key` in `manifest.json` fixes its ID at `lcpmfalaoaicdkagomhfooajfpikkjpa`. |
| `tests/` | End-to-end tests against a local server. The video tests generate real HLS/DASH media with ffmpeg and check that the output decodes cleanly. |

The task list is stored in `%APPDATA%\PyDM\PyDM\downloads.json`.

## Building the installer

```
build_installer.bat
```

The script installs the build tools (PyInstaller and Pillow, from `packaging\requirements-build.txt`), redraws the icons (`tools\make_icons.py`) and bundles the app with `packaging\pydm.spec`. It then compiles `packaging\pydm.iss` with [Inno Setup 6](https://jrsoftware.org/isinfo.php) into `dist\PyDM-Setup-<version>.exe`. Install Inno Setup with `winget install JRSoftware.InnoSetup`. The version comes from `pydm/__init__.py`.

The bundle contains two programs:
- `PyDM.exe`, the window.
- `pydm-host.exe`, a console program the browser talks to over stdio.

Unused Qt parts (software OpenGL, PDF, translations) are left out.

To check a build before shipping it, run `build\dist\PyDM\PyDM.exe --build-check OUT_DIR` (set `PYDM_NO_REGISTER=1` so it doesn't re-point your browsers). It writes `OUT_DIR\build-check.json` with:
- the component versions;
- an off-screen screenshot of the window;
- a real 144p YouTube download, and whether it decodes cleanly.

## Tests

```
.venv\Scripts\python tests\test_engine.py
.venv\Scripts\python tests\test_gui.py [screenshot.png]
.venv\Scripts\python tests\test_browser_bridge.py
.venv\Scripts\python tests\test_streams.py
.venv\Scripts\python tests\test_features.py         # speed limit, clipboard, folders by type, duplicates, video sites
.venv\Scripts\python tests\test_queues.py           # queue order, limits, schedule (simulated clock)
.venv\Scripts\python tests\test_gui_queues.py [prefix]  # queues in the window, the shutdown countdown (power action stubbed)
.venv\Scripts\python tests\test_refresh.py          # refreshing expired links (files and HLS)
.venv\Scripts\python tests\test_updates.py          # yt-dlp updates against a fake PyPI, restart flow
.venv\Scripts\python tests\test_gui_extras.py [prefix]  # connections mid-download, completion window, Defender scan, queues after restart
.venv\Scripts\python tests\test_resilience.py       # errors are logged, the app keeps running; the 20-second update check
.venv\Scripts\python tests\test_gui_select.py [screenshot-prefix]    # multi-select and delete
.venv\Scripts\python tests\test_gui_streams.py [screenshot-prefix]
.venv\Scripts\python tests\test_youtube.py [screenshot-prefix]   # needs internet (~35 MB)
python tests\test_video_button.py        # the Download button on videos, in headless Edge (needs: pip install playwright)
node tests\test_extension.mjs            # extension logic against mocked chrome.* APIs
```

## Ideas for next steps

- **Firefox support.** It needs a separate host manifest that uses `allowed_extensions` and a different registry key.
- **Video quality in the popup.** Let the popup pick a quality, instead of choosing it in the Add dialog.
- **Subtitles.** Download HLS/DASH subtitle tracks and mux them in.
- Batch downloads (`file[001-100].zip`), importing and exporting the list, per-site logins, proxy settings, and checksum verification.

## License

PyDM is free software under the [GNU General Public License v3](LICENSE). The installer bundles third-party components (Qt/PyQt6, FFmpeg, yt-dlp, Deno, Python…) under their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Use it only for downloads you're allowed to make. PyDM refuses DRM-protected video.
