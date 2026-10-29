"""PyDM - a segmented, resumable download manager."""
from . import updates

updates.activate()  # a newer downloaded yt-dlp, if any; must run before anything imports yt_dlp

from .engine import DownloadManager, DownloadTask, Status  # noqa: E402

__all__ = ["DownloadManager", "DownloadTask", "Status"]
__version__ = "1.3.1"
