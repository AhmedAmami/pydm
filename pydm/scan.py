"""Scanning finished downloads with Microsoft Defender's command-line scanner
(MpCmdRun.exe, part of Windows; works without admin rights).

Defender handles anything it finds the way it always does: the file is
quarantined. PyDM only reports the result.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache

# "risky" mode: files that can run code or hide other files.
RISKY_EXTENSIONS = {
    "exe", "msi", "msix", "appx", "bat", "cmd", "com", "scr", "ps1", "vbs", "js", "jse", "wsf", "hta", "jar",
    "dll", "lnk", "reg", "apk", "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "cab", "iso", "img",
    "docm", "xlsm", "pptm",
}
MODES = {"off": "Off", "risky": "Programs and archives", "all": "All files"}


@dataclass
class ScanResult:
    status: str        # "clean", "threat" or "error"
    detail: str = ""   # threat name or error text

    def text(self) -> str:
        return {"clean": "Microsoft Defender: no threats found",
                "threat": f"Microsoft Defender found {self.detail or 'a threat'} and removed the file",
                "error": f"Couldn't scan: {self.detail}"}[self.status]


@lru_cache(maxsize=1)
def scanner() -> str | None:
    """Path to MpCmdRun.exe, or None if Defender isn't there."""
    data = os.environ.get("ProgramData", r"C:\ProgramData")
    candidates = [os.path.join(data, "Microsoft", "Windows Defender", "Platform", "CurrentVersion", "MpCmdRun.exe")]
    candidates += sorted(glob.glob(os.path.join(data, "Microsoft", "Windows Defender", "Platform", "*", "MpCmdRun.exe")),
                         reverse=True)
    candidates.append(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Windows Defender",
                                   "MpCmdRun.exe"))
    return next((c for c in candidates if os.path.isfile(c)), None)


def should_scan(path: str, mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "risky":
        return path.rsplit(".", 1)[-1].lower() in RISKY_EXTENSIONS if "." in os.path.basename(path) else False
    return False


def parse(exit_code: int, output: str) -> ScanResult:
    if exit_code == 0:
        return ScanResult("clean")
    if exit_code == 2:
        m = re.search(r"^\s*Threat\s*:\s*(.+?)\s*$", output, re.M)
        return ScanResult("threat", m.group(1) if m else "")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if any("0x800106ba" in line.lower() or "service" in line.lower() for line in lines):
        return ScanResult("error", "Microsoft Defender is turned off (another antivirus may be in charge)")
    return ScanResult("error", lines[-1] if lines else f"exit code {exit_code}")


def scan(path: str, timeout: float = 600) -> ScanResult:
    exe = scanner()
    if not exe:
        return ScanResult("error", "Microsoft Defender isn't available on this PC")
    if not os.path.exists(path):
        return ScanResult("error", "the file is gone (your antivirus may already have removed it)")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.run([exe, "-Scan", "-ScanType", "3", "-File", path], capture_output=True, text=True,
                           timeout=timeout, creationflags=flags, errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        return ScanResult("error", str(e))
    return parse(p.returncode, (p.stdout or "") + (p.stderr or ""))
