# Third-party software

PyDM is licensed under the GNU General Public License v3 (see `LICENSE`). The
installer bundles the following software, each under its own license.

| Component | License | Source |
|---|---|---|
| Python 3.14 | PSF License | https://www.python.org/ |
| Qt 6 and PyQt6 | GPL v3 (PyQt6), LGPL v3 (Qt) | https://www.riverbankcomputing.com/software/pyqt/ · https://www.qt.io/ |
| FFmpeg 7.1 (essentials build by gyan.dev, via imageio-ffmpeg) | GPL v3 | https://ffmpeg.org/ · https://www.gyan.dev/ffmpeg/builds/ · https://github.com/imageio/imageio-ffmpeg |
| yt-dlp and yt-dlp-ejs | Unlicense | https://github.com/yt-dlp/yt-dlp · https://github.com/yt-dlp/ejs |
| Deno | MIT | https://github.com/denoland/deno |
| requests, urllib3, idna, certifi | Apache 2.0, MIT, BSD, MPL 2.0 | https://github.com/psf/requests |
| Brotli | MIT | https://github.com/google/brotli |
| mutagen | GPL v2 or later | https://github.com/quodlibet/mutagen |
| pycryptodomex | BSD / Public Domain | https://github.com/Legrandin/pycryptodome |
| websockets | BSD | https://github.com/python-websockets/websockets |
| imageio-ffmpeg | BSD 2-Clause | https://github.com/imageio/imageio-ffmpeg |

The source code of each component is available at the links above. The exact
FFmpeg source matching the bundled build is linked from the gyan.dev builds page.
PyInstaller (GPL with an exception for bundled apps) and Inno Setup were used to
build the installer and are not part of it.
