"""TikTok: individual videos, including vm./vt. short links.

yt-dlp ranks watermark-free formats above watermarked ones on its own. At
equal resolution we prefer H.264 over H.265 (bytevc1) for NLE compatibility.
"""
from .base import FormatChoice, Platform, audio_choice

PLATFORM = Platform(
    key="tiktok",
    name="TikTok",
    accent="#25f4ee",
    accent_text="#05181a",
    hosts=("tiktok.com",),
    example="https://www.tiktok.com/@user/video/…",
    outtmpl="%(uploader)s - %(title).80B [%(id)s].%(ext)s",
    rename="@%(uploader)s",  # TikTok's uploader field is the @handle
    formats=[
        FormatChoice("Best quality (MP4)", {
            "format": "bv*+ba/b",
            "format_sort": ["res", "vcodec:h264"],
            "merge_output_format": "mp4",
        }),
        # Higher resolutions are often H.265 only; this trades resolution for compatibility.
        FormatChoice("H.264 MP4 (most compatible)", {
            "format": "bv*+ba/b",
            "format_sort": ["vcodec:h264", "res"],
            "merge_output_format": "mp4",
        }),
        audio_choice("Audio only (M4A)", "m4a"),
        audio_choice("Audio only (WAV)", "wav"),
    ],
    extra_options={
        "noplaylist": True,
    },
    hints=(
        ("photo", "This looks like a photo slideshow. Only videos can be downloaded."),
        ("status code 10204", "TikTok says this video isn't available (deleted or private)."),
        ("status code 10222", "This account is private. Set Cookies to Firefox or a cookies.txt file."),
        ("unable to find video in feed", "TikTok didn't return the video. Retry, or set Cookies."),
    ),
)
