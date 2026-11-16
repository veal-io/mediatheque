"""Shared definition of a platform.

Each platform (YouTube, TikTok, Instagram) lives in its own module and only
describes itself: which links it accepts, which quality presets it offers, how
files are named, and how to explain its typical errors. If a platform changes
something, the fix belongs in that platform's module and nowhere else.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse


@dataclass(frozen=True)
class FormatChoice:
    label: str
    options: dict


def audio_choice(label: str, codec: str) -> FormatChoice:
    return FormatChoice(label, {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": codec}],
    })


# Hints that apply to every platform. Platform modules can add their own,
# which are checked first. Matching is case-insensitive on the error text.
COMMON_HINTS: tuple[tuple[str, str], ...] = (
    ("could not copy chrome cookie database", "Mediatheque's browser is open (the Sign In window or a capture), so "
                                              "its sign-ins can't be read. Close it, then Retry."),
    ("cookies database", "Couldn't find Firefox's cookies. Is Firefox installed and signed in?"),
    ("netscape format", "That cookies file isn't valid. Re-export it with 'Get cookies.txt LOCALLY'."),
    ("unsupported url", "That link isn't a video this tab understands."),
    ("http error 429", "The platform is rate-limiting you. Wait a few minutes, or use Cookies."),
    ("http error 403", "Access was refused. Try again, try Cookies, or run Tools > Update Engine."),
    ("requested format is not available", "That quality isn't available for this video. Pick another."),
    ("private video", "This video is private."),
    ("video unavailable", "This video is unavailable (removed, private, or region-locked)."),
    ("unable to extract", "The platform may have changed its site. Try Tools > Update Engine."),
    ("getaddrinfo failed", "No internet connection."),
    ("timed out", "The connection timed out. Try again."),
)


@dataclass
class Platform:
    key: str
    name: str
    accent: str            # tab accent colour
    accent_text: str       # text colour that reads well on the accent
    hosts: tuple[str, ...]
    example: str
    outtmpl: str
    formats: list[FormatChoice]
    # Optional final name (yt-dlp template, no extension) applied once the file is finished.
    # Duplicates are numbered: "@user.mp4", "@user (2).mp4", …
    rename: str | None = None
    # Optional on-screen source credit for a finished download, built from its metadata.
    source: Callable[[dict], str] | None = None
    extra_options: dict = field(default_factory=dict)
    hints: tuple[tuple[str, str], ...] = ()
    # Optional: only links whose address matches this regex belong here (e.g. Facebook
    # videos and reels, but not written posts, which go to the Screenshotter).
    url_pattern: str = ""
    # Optional: inside scripts, only these links count as videos (the rest are captured as
    # articles). Reddit posts are usually quoted as posts, so only v.redd.it links qualify.
    script_pattern: str = ""
    # Optional: turn a link into the one yt-dlp understands (e.g. follow Reddit share links).
    expand: Callable[[str], str] | None = None

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in self.hosts):
            return False
        return not self.url_pattern or re.search(self.url_pattern, url, re.I) is not None

    @property
    def format_labels(self) -> list[str]:
        return [f.label for f in self.formats]

    def build_options(self, format_label: str) -> dict:
        choice = next((f for f in self.formats if f.label == format_label), self.formats[0])
        opts = copy.deepcopy(self.extra_options)
        opts.update(copy.deepcopy(choice.options))
        opts["outtmpl"] = self.outtmpl
        return opts

    def explain(self, error: str) -> str:
        low = error.lower()
        for needle, hint in (*self.hints, *COMMON_HINTS):
            if needle in low:
                return hint
        # Fall back to yt-dlp's own message, minus its prefix.
        line = error.strip().splitlines()[-1] if error.strip() else "Unknown error"
        return line.removeprefix("ERROR: ")


def normalize_url(text: str) -> str:
    text = text.strip().strip("<>\"'")
    if "://" not in text:
        text = "https://" + text
    return text
