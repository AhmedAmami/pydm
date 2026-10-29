## PyDM 1.3.1

- **Fixed: a dubbed language could download in the original language.** When the video came from the browser (signed in to YouTube), some videos were saved with their original audio even though another language was picked. Now the chosen language is used.

## PyDM 1.3.0

- **Videos in other languages.** Many YouTube videos come dubbed (MrBeast's in over 20 languages, for example). When a video has dubbed audio, the Add window shows an **Audio** choice next to Quality: pick Spanish, Arabic, French... and PyDM downloads the video with that soundtrack, saved as *Title (Spanish).mp4* next to the original.
- **Prefer a language.** *Settings → Downloads → Prefer video audio in* picks your language automatically whenever a video has it; otherwise you get the original.

## PyDM 1.2.0

- **PyDM updates itself.** When a new version is out, an **Update** button appears at the bottom of the window. Click it and then **Update now**: PyDM downloads the new installer from GitHub, checks it against the checksum GitHub publishes, installs it and opens again. Downloads that were running pause and carry on afterwards. You can also check by hand under *Settings → About*.
- After this version, there's no need to download installers from GitHub by hand.

## PyDM 1.1.1

- **Smoother scrolling on fast monitors.** The list now scrolls at your screen's refresh rate (120, 144, 165 Hz…) instead of about 60 frames per second, and quick wheel spins flow into each other.

## PyDM 1.1.0

### What's new
- **A cleaner download list.** Each download has two lines: the name, then its progress, speed and time left, or its size, site and date once finished. Each one also has a coloured status label and a slim progress bar.
- **Buttons on the row under the mouse:** pause or resume, open, show in folder, and more.
- **Video thumbnails.** YouTube and other videos show their thumbnail in the list. The Add window shows a preview with the title, length and channel before you pick the quality.
- **The title bar matches the app**, including in dark mode, on Windows 11.
- **Clearer errors.** For example, "Not found on the server (404)" or "Access denied: the link may have expired (403)".
- **Compact view.** Prefer the old columns? Use the view button next to Settings.
- **Dark mode in one click.** Click the moon/sun button in the toolbar. Its small arrow also offers *Match Windows*.
- **Smoother scrolling.** One row per mouse-wheel notch, with a short glide.

If you already have PyDM, just run the new installer: your downloads and settings stay.


PyDM is a free download manager for Windows. It downloads files faster by using several connections at once, and it can pause and resume them, even after a restart. It also saves videos from YouTube, Facebook, Instagram, X, TikTok, Vimeo and many other sites.

### Install

1. Download **PyDM-Setup-1.3.1.exe** below, under *Assets*.
2. Run it. Windows may show **"Windows protected your PC"** because the installer isn't signed. Click **More info**, then **Run anyway**.
3. Click through the setup. It installs only for you, so it doesn't need an administrator.

### Add PyDM to your browser (Brave, Chrome or Edge)

With this, downloads and videos go to PyDM by themselves.

1. Open PyDM, then **Settings → Browser**. That page shows the steps and has a button that opens the extension folder.
2. In the browser, open `brave://extensions` (or `chrome://extensions`, `edge://extensions`) and turn on **Developer mode**.
3. Click **Load unpacked** and choose the folder PyDM showed you (`%LOCALAPPDATA%\Programs\PyDM\browser_extension`).
4. Pin the PyDM icon to the toolbar.

### Using it

- **Files:** download as usual in the browser. PyDM takes over, or click **Add URL** in PyDM and paste a link.
- **Videos:** move the mouse over a video and click the blue **Download** button that appears on it, then pick a quality.
- **Instagram and some Facebook videos** need you signed in to the site in your browser. Download those from the browser so your login comes along.
- **Queues and scheduling:** download overnight, and optionally shut down the PC when done (**Scheduler** button).
- New downloads are checked by Microsoft Defender automatically.

### Good to know

- Videos protected by DRM (Netflix, Disney+, Prime Video, Spotify…) can't be downloaded.
- Only download what you're allowed to save.
- PyDM keeps its video downloader (yt-dlp) up to date by itself.
- If something goes wrong: in PyDM, open **Settings → About → Open log folder** and send the `pydm.log` file.
- To uninstall: Windows **Settings → Apps → PyDM download manager**.
