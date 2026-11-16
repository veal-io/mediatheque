"""YouTube: videos, Shorts, and YouTube Music links.

Format notes for editing: YouTube only serves H.264 up to 1080p. Above that
it's VP9 or AV1, which Premiere can't read, so those files are converted to
H.264 after download (see convert.py). At equal resolution H.264 is preferred,
so nothing at 1080p or below ever needs converting. Audio prefers AAC (m4a).
"""
from .base import FormatChoice, Platform, audio_choice


def _source(info: dict) -> str:
    """On-screen credit for YouTube clips: "Title | Channel"."""
    return f"{info.get('title') or ''} | {info.get('channel') or info.get('uploader') or ''}"


def _video(sort: list[str]) -> dict:
    return {
        "format": "bv*+ba/b",
        "format_sort": sort,
        "merge_output_format": "mp4",
    }


PLATFORM = Platform(
    key="youtube",
    name="YouTube",
    accent="#ff3b3b",
    accent_text="#ffffff",
    hosts=("youtube.com", "youtu.be", "youtube-nocookie.com"),
    example="https://www.youtube.com/watch?v=…",
    outtmpl="%(title).150B - %(channel,uploader)s.%(ext)s",
    source=_source,
    formats=[
        FormatChoice("Best quality (up to 4K+)", _video(["res", "fps", "vcodec:h264", "acodec:m4a"])),
        FormatChoice("2160p (4K)", _video(["res:2160", "fps", "vcodec:h264", "acodec:m4a"])),
        FormatChoice("1440p", _video(["res:1440", "fps", "vcodec:h264", "acodec:m4a"])),
        FormatChoice("1080p H.264 (most compatible)", _video(["vcodec:h264", "res:1080", "fps", "acodec:m4a"])),
        FormatChoice("720p H.264", _video(["vcodec:h264", "res:720", "fps", "acodec:m4a"])),
        audio_choice("Audio only (M4A)", "m4a"),
        audio_choice("Audio only (WAV)", "wav"),
    ],
    extra_options={
        # A watch link that's part of a playlist downloads just that video.
        "noplaylist": True,
    },
    hints=(
        ("sign in to confirm you", "YouTube wants proof you're not a bot. Sign in to YouTube in Firefox, set Cookies "
                                   "to Firefox, then Retry."),
        ("sign in to confirm your age", "Age-restricted. Sign in to YouTube in Firefox, set Cookies to Firefox, then Retry."),
        ("members-only", "Members-only video. Sign in to YouTube in Firefox and set Cookies to Firefox."),
        ("this live event will begin", "This livestream hasn't started yet."),
        ("n challenge", "YouTube changed its player. Try Tools > Update Engine."),
    ),
)
