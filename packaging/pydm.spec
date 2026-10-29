# PyInstaller spec: builds PyDM.exe (window) and pydm-host.exe (browser bridge,
# console app so the browser can talk to it over stdio) into one folder.
#   .venv\Scripts\pyinstaller packaging\pydm.spec --distpath build\dist --workpath build\work
import re
from pathlib import Path

import deno
import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "pydm" / "assets" / "pydm.ico")
VERSION = re.search(r'__version__ = "([\d.]+)"', (ROOT / "pydm" / "__init__.py").read_text()).group(1)
EXCLUDES = ["tkinter", "unittest", "pydoc", "test", "PIL", "PyInstaller"]


def version_info(description, filename):
    nums = tuple(int(x) for x in (VERSION + ".0.0.0").split(".")[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "PyDM"), StringStruct("FileDescription", description),
            StringStruct("FileVersion", VERSION), StringStruct("ProductName", "PyDM"),
            StringStruct("ProductVersion", VERSION), StringStruct("OriginalFilename", filename),
            StringStruct("LegalCopyright", "PyDM contributors")])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])])])


app = Analysis(
    [str(ROOT / "packaging" / "pydm_app.py")],
    pathex=[str(ROOT)],
    binaries=[(imageio_ffmpeg.get_ffmpeg_exe(), "imageio_ffmpeg/binaries"),  # merging videos
              (deno.find_deno_bin(), ".")],                                   # yt-dlp's JS runtime
    # yt-dlp's metadata: the updater compares the bundled version with downloaded ones.
    datas=[(str(ROOT / "pydm" / "assets"), "pydm/assets"), *collect_data_files("yt_dlp_ejs"),
           *copy_metadata("yt-dlp")],
    hiddenimports=["pydm.ytdl", "pydm.streams", *collect_submodules("yt_dlp_ejs")],
    excludes=EXCLUDES,
)
host = Analysis(
    [str(ROOT / "packaging" / "pydm_host.py")],
    pathex=[str(ROOT)],
    excludes=EXCLUDES + ["yt_dlp", "imageio_ffmpeg", "PyQt6.QtWidgets", "PyQt6.QtGui", "PyQt6.QtSvg"],
)

# Unused parts of Qt: software OpenGL fallback, PDF support, translations (~30 MB).
DROP = ("opengl32sw.dll", "qt6pdf.dll", "qpdf.dll", "/translations/")


def keep(entry):
    dest = entry[0].replace("\\", "/").lower()
    return not any(d in dest for d in DROP)


for a in (app, host):
    a.binaries = [b for b in a.binaries if keep(b)]
    a.datas = [d for d in a.datas if keep(d)]

app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="PyDM", icon=ICON, console=False,
              version=version_info("PyDM download manager", "PyDM.exe"), upx=False)
host_exe = EXE(PYZ(host.pure), host.scripts, [], exclude_binaries=True, name="pydm-host", icon=ICON, console=True,
               version=version_info("PyDM browser connector", "pydm-host.exe"), upx=False)
COLLECT(app_exe, app.binaries, app.datas, host_exe, host.binaries, host.datas, name="PyDM", upx=False)
