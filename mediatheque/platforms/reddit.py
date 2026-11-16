"""Reddit: videos in posts, plus v.redd.it links.

Post addresses: reddit.com/r/<sub>/comments/<id>/…, /user/<name>/comments/…, old.reddit.com,
redd.it/<id> short links and the Share button's reddit.com/r/<sub>/s/<code> links (these
are expanded to the post first). Reddit wants an account to hand out videos, so set Cookies
to Firefox after signing in to Reddit there.

In a script, Reddit posts are usually quoted as posts, so the Script tool and Screenshots
treat them as articles to capture. Only v.redd.it links count as videos there
(script_pattern); paste a post into the Downloader to download its video.
"""
from __future__ import annotations

import urllib.request

from .base import FormatChoice, Platform, audio_choice


def _expand(url: str) -> str:
    """Share links (/s/…) and redd.it short links redirect to the post: follow them."""
    if "/s/" not in url and "redd.it/" not in url.replace("v.redd.it/", ""):
        return url
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 Mediatheque"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.geturl() or url
    except Exception:
        return url


def _source(info: dict) -> str:
    """On-screen credit for Reddit clips: "u/name · r/subreddit"."""
    user = info.get("uploader") or ""
    sub = info.get("channel_id") or ""
    parts = [f"u/{user}" if user else "", f"r/{sub}" if sub and not sub.startswith("u_") else ""]
    return " · ".join(p for p in parts if p)


PLATFORM = Platform(
    key="reddit",
    name="Reddit",
    accent="#ff4500",
    accent_text="#ffffff",
    hosts=("reddit.com", "redd.it", "redditmedia.com"),
    url_pattern=r"/comments/|/r/[^/]+/s/|^https?://(?:www\.)?redd\.it/|v\.redd\.it/",
    script_pattern=r"v\.redd\.it/",
    example="https://www.reddit.com/r/…/comments/…",
    outtmpl="%(title).100B - u_%(uploader)s [%(id)s].%(ext)s",
    source=_source,
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
    extra_options={"noplaylist": True},
    hints=(
        ("authentication is required", "Reddit wants you signed in. Sign in to Reddit in Firefox, set Cookies to "
                                       "Firefox, then Retry."),
        ("no media found", "No video in that post. It's a text or image post, so it belongs in Screenshots."),
        ("no video formats found", "No video in that post. Is it a text or image post? Those go to Screenshots."),
        ("unsupported url", "That Reddit link isn't a post. Copy the post's link (Share > Copy link) and try again."),
    ),
)
