from .base import Platform, normalize_url
from .facebook import PLATFORM as FACEBOOK
from .instagram import PLATFORM as INSTAGRAM
from .reddit import PLATFORM as REDDIT
from .tiktok import PLATFORM as TIKTOK
from .x import PLATFORM as X
from .youtube import PLATFORM as YOUTUBE

# The order platforms are listed in (View Log, messages).
PLATFORMS: list[Platform] = [YOUTUBE, TIKTOK, INSTAGRAM, FACEBOOK, REDDIT, X]


def platform_for(url: str) -> Platform | None:
    return next((p for p in PLATFORMS if p.matches(url)), None)


def video_platform_in_script(url: str) -> Platform | None:
    """Like platform_for, for links found in scripts: a platform can count only some of its
    links as videos there (Reddit posts are captured as articles; v.redd.it links are videos)."""
    import re
    p = platform_for(url)
    if p and p.script_pattern and not re.search(p.script_pattern, url, re.I):
        return None
    return p


def needs_lookup(url: str) -> bool:
    """A post whose address doesn't say whether it holds a video (X and Reddit posts)."""
    return platform_for(url) is not None and video_platform_in_script(url) is None


# One Quality choice for every platform (the Downloader is a single page). Each maps to the
# closest preset a platform has: resolutions are YouTube's, TikTok has its own H.264 MP4,
# and Instagram and Facebook only offer their best MP4 or audio.
BEST, Q2160, Q1440, Q1080, Q720 = ("Best quality", "4K (2160p, YouTube)", "1440p (YouTube)",
                                   "1080p H.264 (most compatible)", "720p H.264")
AUDIO_M4A, AUDIO_WAV = "Audio only (M4A)", "Audio only (WAV)"
QUALITIES = [BEST, Q2160, Q1440, Q1080, Q720, AUDIO_M4A, AUDIO_WAV]
_PRESETS = {
    "youtube": {BEST: "Best quality (up to 4K+)", Q2160: "2160p (4K)", Q1440: "1440p",
                Q1080: "1080p H.264 (most compatible)", Q720: "720p H.264"},
    "tiktok": {Q1080: "H.264 MP4 (most compatible)", Q720: "H.264 MP4 (most compatible)"},
}


def format_for(platform: Platform, quality: str) -> str:
    """The platform's own preset for a Downloader Quality choice."""
    label = _PRESETS.get(platform.key, {}).get(quality, quality)
    return label if label in platform.format_labels else platform.format_labels[0]


def quality_from_preset(label: str) -> str:
    """Translate a preset saved by the old per-platform tabs into a Quality choice."""
    for presets in _PRESETS.values():
        for quality, preset in presets.items():
            if preset == label:
                return quality
    return label if label in QUALITIES else BEST


__all__ = ["PLATFORMS", "Platform", "normalize_url", "platform_for", "video_platform_in_script", "needs_lookup", "QUALITIES", "format_for",
           "quality_from_preset"]
