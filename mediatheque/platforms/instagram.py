"""Instagram: Reels, video posts, carousels (videos only), and IGTV.

Instagram increasingly requires a logged-in session even for public reels;
the Cookies option on this tab is the usual fix.
"""
from .base import FormatChoice, Platform, audio_choice

PLATFORM = Platform(
    key="instagram",
    name="Instagram",
    accent="#e1306c",
    accent_text="#ffffff",
    hosts=("instagram.com", "instagr.am"),
    example="https://www.instagram.com/reel/…",
    outtmpl="%(channel,uploader)s - %(description,title).70B [%(id)s].%(ext)s",
    rename="@%(channel)s",  # Instagram's channel field is the @handle; uploader is the display name
    formats=[
        FormatChoice("Best quality (MP4)", {
            # Instagram's high-res streams are VP9 (converted to H.264 after download).
            # When they're no sharper than 720p (on the shorter side, as reels are vertical),
            # take the plain H.264 + AAC file ("1") instead; yt-dlp can't see its resolution.
            "format": "bv*[width>720][height>720]+ba/1/bv*+ba/b",
            "format_sort": ["res", "vcodec:h264"],
            "merge_output_format": "mp4",
        }),
        audio_choice("Audio only (M4A)", "m4a"),
        audio_choice("Audio only (WAV)", "wav"),
    ],
    extra_options={
        # A carousel post is a "playlist": grab every video in it.
        "noplaylist": False,
    },
    hints=(
        ("login required", "Instagram wants you signed in. Set Cookies to Firefox or a cookies.txt file."),
        ("empty media response",
         "Instagram wants you signed in. Set Cookies to Firefox or a cookies.txt file."),
        ("rate-limit reached", "Instagram wants you signed in. Set Cookies to Firefox or a cookies.txt file."),
        ("there is no video in this post", "This post has no video in it, only images."),
        ("restricted video", "Restricted post. Set Cookies to Firefox or a cookies.txt file."),
    ),
)
