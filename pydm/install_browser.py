"""Register the native messaging host with Chrome, Edge and Brave (per user, no admin).

    python -m pydm.install_browser             install
    python -m pydm.install_browser --uninstall remove

From source, the browser runs native_host/pydm_host.bat (the project's Python).
When installed (frozen by PyInstaller), it runs pydm-host.exe next to PyDM.exe,
and the manifest lives in %LOCALAPPDATA%\\PyDM because the program folder may be
read-only. PyDM.exe re-registers itself on every start, so the browser always
points at the copy that is actually installed.
"""
import json
import os
import sys
import winreg
from pathlib import Path

HOST_NAME = "com.pydm.host"
# Fixed by the "key" in browser_extension/manifest.json.
EXTENSION_ID = "lcpmfalaoaicdkagomhfooajfpikkjpa"
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
HOST_DIR = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PyDM" / "native_host") if FROZEN \
    else ROOT / "native_host"
MANIFEST = HOST_DIR / f"{HOST_NAME}.json"
BROWSER_KEYS = {
    "Chrome": rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}",
    "Edge": rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}",
    "Brave": rf"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\{HOST_NAME}",
}


def extension_dir() -> Path:
    return ROOT / "browser_extension"


def _host_program() -> Path:
    if FROZEN:
        return ROOT / "pydm-host.exe"
    launcher = HOST_DIR / "pydm_host.bat"
    # Anything the .bat echoes would corrupt the stdio protocol, hence @echo off.
    launcher.write_text(
        "@echo off\r\n"
        f'cd /d "{ROOT}"\r\n'
        f'"{Path(sys.executable)}" -m pydm.native_host\r\n',
        encoding="utf-8")
    return launcher


def install(quiet: bool = False) -> None:
    HOST_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "name": HOST_NAME,
        "description": "PyDM download manager",
        "path": str(_host_program()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }, indent=2), encoding="utf-8")
    for browser, key in BROWSER_KEYS.items():
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValue(k, "", winreg.REG_SZ, str(MANIFEST))
        if not quiet:
            print(f"Registered with {browser}")
    if quiet:
        return
    print(f"""
Native host installed.

Now load the extension:
  1. Open chrome://extensions (or edge://extensions, brave://extensions)
  2. Turn on "Developer mode"
  3. Click "Load unpacked" and choose:
       {extension_dir()}
  4. Click the PyDM toolbar icon, then "Test connection"
""")


def is_installed() -> bool:
    """True if every browser points at a manifest whose host program exists."""
    for key in BROWSER_KEYS.values():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                manifest = Path(winreg.QueryValue(k, ""))
            host = Path(json.loads(manifest.read_text(encoding="utf-8"))["path"])
        except (OSError, ValueError, KeyError):
            return False
        if not host.exists():
            return False
    return True


def uninstall(quiet: bool = False) -> None:
    for browser, key in BROWSER_KEYS.items():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                registered = winreg.QueryValue(k, "")
            if Path(registered) != MANIFEST:
                continue  # another copy of PyDM owns it
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            if not quiet:
                print(f"Removed from {browser}")
        except OSError:
            pass
    for f in (HOST_DIR / "pydm_host.bat", MANIFEST):
        f.unlink(missing_ok=True)
    if not quiet:
        print("Native host uninstalled. Remove the extension from the browser's extensions page.")


if __name__ == "__main__":
    uninstall() if "--uninstall" in sys.argv else install()
