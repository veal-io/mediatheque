"""X (Twitter): videos in posts.

Post addresses: x.com/<handle>/status/<id> (also twitter.com, mobile.twitter.com, and the
fxtwitter / vxtwitter / fixupx share mirrors, which are turned back into x.com links).
Files are named after the poster's @handle, like TikTok and Instagram. A post with several
videos downloads them all: "@handle.mp4", "@handle (2).mp4"…

In a script, X posts are usually quoted as posts, so the Script tool and Screenshots keep
capturing them as articles (script_pattern never matches); paste a post into the
Downloader to download its video.
"""
from __future__ import annotations

import re

from .base import FormatChoice, Platform, audio_choice

_MIRRORS = r"(?:fx|vx)twitter\.com|fixupx\.com|fixvx\.com|twittpr\.com"


def _expand(url: str) -> str:
    """Share mirrors (fxtwitter.com, vxtwitter.com, fixupx.com…) -> the real x.com post."""
    return re.sub(rf"^https?://(?:www\.|m\.)?(?:{_MIRRORS})/", "https://x.com/", url, flags=re.I)


PLATFORM = Platform(
    key="x",
    name="X",
    accent="#000000",
    accent_text="#ffffff",
    hosts=("x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com", "fixvx.com", "twittpr.com"),
    url_pattern=r"/status(?:es)?/\d+",
    script_pattern=r"(?!)",  # never: in scripts, X posts are captured as articles
    example="https://x.com/…/status/…",
    outtmpl="%(uploader_id)s - %(title).80B [%(id)s].%(ext)s",
    rename="@%(uploader_id)s",  # X's uploader_id is the @handle
    expand=_expand,
    formats=[
        FormatChoice("Best quality (MP4)", {
            "format": "bv*+ba/b",
            "format_sort": ["res", "vcodec:h264"],
            "merge_output_format": "mp4",
        }),
        audio_choice("Audio only (M4A)", "m4a"),
        audio_choice("Audio only (WAV)", "wav"),
    ],
    hints=(
        ("no video could be found", "That post has no video (it's text or a picture). Posts go to Screenshots."),
        ("nsfw", "X only shows this post to signed-in accounts. Sign in to X in Firefox, set Cookies to Firefox, "
                 "then Retry."),
        ("age-restricted", "X only shows this post to signed-in accounts. Sign in to X in Firefox, set Cookies to "
                           "Firefox, then Retry."),
        ("protected", "That account is private, so its videos can't be downloaded."),
        ("suspended", "That account is suspended, so its posts are gone."),
        ("requested tweet is unavailable", "That post was deleted or isn't public."),
    ),
)
