"""Dubbed audio tracks: the language list, the format choice (yt-dlp's own selector
on made-up formats, so nothing is downloaded) and the Audio row of the Add dialog.
Run from the project root: .venv/Scripts/python tests/test_audio_language.py
"""
import os
import sys
import uuid

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["PYDM_IPC_NAME"] = f"pydm-test-{uuid.uuid4().hex[:8]}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

import yt_dlp  # noqa: E402
from PyQt6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import theme, ytdl  # noqa: E402
from pydm.gui import AddDownloadDialog  # noqa: E402
from pydm.streams import Variant  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def audio(fid, lang, note, pref=-1, ext="m4a"):
    return {"format_id": fid, "url": f"https://x.invalid/{fid}", "ext": ext, "vcodec": "none", "acodec": "mp4a",
            "language": lang, "format_note": note, "language_preference": pref, "abr": 128}


def video(fid, height):
    return {"format_id": fid, "url": f"https://x.invalid/{fid}", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
            "height": height, "width": height * 16 // 9}


FORMATS = [
    {"format_id": "18", "url": "https://x.invalid/18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
     "height": 360, "language": "en", "format_note": "360p"},
    audio("en", "en", "English original (default), medium", 10),
    audio("es", "es", "Spanish, medium"),
    audio("ar", "ar", "Arabic, medium"),
    audio("pt-BR", "pt-BR", "Portuguese (Brazil), medium"),
    audio("frw", "fr", "French, medium", ext="webm"),
    video("v144", 144), video("v720", 720),
]


def pick(variant):
    info = {"id": "x", "title": "x", "formats": [dict(f) for f in FORMATS], "extractor": "test",
            "extractor_key": "Test", "webpage_url": "https://x.invalid/"}
    with yt_dlp.YoutubeDL({"quiet": True, "format": ytdl._format_for(variant), "simulate": True}) as ydl:
        r = ydl.process_ie_result(info, download=False)
    return [f["format_id"] for f in r.get("requested_formats") or [r]]


langs = ytdl.audio_languages(FORMATS)
check("original first, then by name", [c for c, _, _ in langs] == ["en", "ar", "fr", "pt-BR", "es"], str(langs))
check("names come from audio tracks", langs[0][1:] == ("English", True) and ("es", "Spanish", False) in langs)
check("one language: nothing to choose", ytdl.audio_languages([audio("en", "en", "English")]) == [])

check("split quality@lang", ytdl.split_variant("720@pt-BR") == ("720", "pt-BR"))
check("plain quality unchanged", ytdl.split_variant("720") == ("720", None))
check("odd language code ignored", ytdl.split_variant("720@x]/b") == ("720@x]/b", None))

check("default keeps the original", pick("720") == ["v720", "en"], str(pick("720")))
check("Spanish dub chosen", pick("720@es") == ["v720", "es"], str(pick("720@es")))
check("height still respected", pick("144@ar") == ["v144", "ar"], str(pick("144@ar")))
check("regional version found", pick("best@pt") == ["v720", "pt-BR"], str(pick("best@pt")))
check("missing language falls back to original", pick("720@de") == ["v720", "en"], str(pick("720@de")))
check("audio only, dubbed", pick("audio@es") == ["es"], str(pick("audio@es")))
check("audio only, webm when that's the only one", pick("audio@fr") == ["frw"], str(pick("audio@fr")))

# Signed in (browser cookies), YouTube also lists HLS videos with the original audio built in.
# Picked as "best video", yt-dlp drops the separate dubbed track and the download is in English.
FORMATS += [{"format_id": "hls1080", "url": "https://x.invalid/hls1080", "ext": "mp4", "vcodec": "avc1",
             "acodec": "mp4a", "height": 1080, "width": 1920, "language": "en", "format_note": "1080p"},
            video("v1080", 1080)]
check("dub at 1080 with a built-in-audio video around", pick("1080@es") == ["v1080", "es"], str(pick("1080@es")))
check("best with a dub too", pick("best@ar") == ["v1080", "ar"], str(pick("best@ar")))
check("no dub: unchanged choice", pick("1080")[0] in ("hls1080", "v1080"), str(pick("1080")))

# --- the Add dialog -----------------------------------------------------------
app = QApplication([])
app.setApplicationName("PyDM-lang-test")
app.setOrganizationName("PyDM-lang-test")
theme.apply(app, "light")
settings = QSettings("PyDM-lang-test", "PyDM-lang-test")
settings.clear()
URL = "https://www.youtube.com/watch?v=dubbedtest1"
QUALITIES = [Variant("720", "720p"), Variant("144", "144p"), Variant("audio", "Audio only (m4a)")]


def dialog(variants=QUALITIES, languages=langs):
    ytdl._meta[URL] = {"title": "Dubbed", "languages": languages}
    dlg = AddDownloadDialog(settings, "https://example.com/file.zip")
    dlg.kind, dlg._probed_url = "ytdl", URL
    dlg._probe_results.append((dlg._probe_token, ("ytdl", variants), None))
    dlg._drain_probe()
    return dlg


dlg = dialog()
check("Audio row shown", dlg.form.isRowVisible(dlg.audio_lang) and dlg.audio_lang.count() == 5)
check("original selected by default", dlg.audio_lang.currentText() == "English (original)" and dlg.variant() == "720")
check("note mentions the languages", "5 audio languages" in dlg.stream_note.text(), dlg.stream_note.text())
dlg.audio_lang.setCurrentIndex(dlg.audio_lang.findData("es"))
check("picked language goes with the quality", dlg.variant() == "720@es")
dlg.quality.setCurrentIndex(dlg.quality.findData("audio"))
check("and with audio only", dlg.variant() == "audio@es")

settings.setValue("audio_language", "pt")
dlg = dialog()
check("preferred language picked (regional match)", dlg.variant() == "720@pt-BR", str(dlg.variant()))
settings.setValue("audio_language", "de")
check("preferred language missing: original", dialog().variant() == "720")

posts = [Variant("item:1", "Video 1 of 2"), Variant("item:2", "Video 2 of 2")]
dlg = dialog(posts)
check("no Audio row for multi-video posts", not dlg.form.isRowVisible(dlg.audio_lang) and dlg.variant() == "item:1")
dlg = dialog(languages=[])
check("no Audio row without dubs", not dlg.form.isRowVisible(dlg.audio_lang) and dlg.variant() == "720")
settings.clear()

print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
