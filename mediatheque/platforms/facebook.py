"""Facebook: videos and reels. Written posts aren't videos: they go to the Screenshotter.

Video addresses: facebook.com/watch?v=…, /watch/…, /reel/…, /videos/…, video.php,
share links /share/v/… and /share/r/…, and fb.watch short links. Everything else on
facebook.com (/posts/…, permalink.php, /share/p/…, photos) is treated as a post.
"""
from .base import FormatChoice, Platform, audio_choice


def _source(info: dict) -> str:
    """On-screen credit for Facebook clips: the page or person that posted it."""
    return info.get("uploader") or info.get("channel") or ""

PLATFORM = Platform(
    key="facebook",
    name="Facebook",
    accent="#1877f2",
    accent_text="#ffffff",
    hosts=("facebook.com", "fb.watch", "fb.com"),
    url_pattern=r"fb\.watch/|/watch\b|/reels?/|/videos?/|video\.php|/share/[vr]/",
    example="https://www.facebook.com/reel/…",
    outtmpl="%(uploader)s - %(title).80B [%(id)s].%(ext)s",
    source=_source,
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
        ("login", "Facebook wants you signed in. Set Cookies to Firefox or a cookies.txt file."),
        ("cannot parse data", "Facebook changed its page. Try Tools > Update Engine, or set Cookies."),
        ("no video formats found", "No video found at that link. Is it a written post rather than a video?"),
    ),
)
