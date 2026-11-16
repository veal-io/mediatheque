"""Bringing images into a project's Images folder: files from anywhere on disk, or images
dragged straight off a web page. File names are kept (a clash gets " (2)")."""
from __future__ import annotations

import mimetypes
import os
import re
import shutil
import urllib.request
from urllib.parse import unquote, urlparse

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".psd", ".avif", ".heic", ".svg"}
_TYPE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
             "image/avif": ".avif", "image/svg+xml": ".svg", "image/bmp": ".bmp", "image/tiff": ".tif"}


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def unique_in(folder: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
    return path


def same_folder(path: str, folder: str) -> bool:
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(folder))


def bring_in(path: str, folder: str) -> str:
    """An image already in the Images folder is used where it is; anything else is copied in."""
    if same_folder(path, folder):
        return os.path.abspath(path)
    os.makedirs(folder, exist_ok=True)
    dest = unique_in(folder, os.path.basename(path))
    shutil.copy2(path, dest)
    return dest


def download(url: str, folder: str) -> str:
    """Save an image from the web into the folder, named after the image's own file name."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/140.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = r.read()
    if not ctype.startswith("image/"):
        raise ValueError("That link isn't an image (it's a web page). Drag the picture itself.")
    name = unquote(os.path.basename(urlparse(url).path)) or "image"
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .") or "image"
    stem, ext = os.path.splitext(name)
    if ext.lower() not in IMAGE_EXTS:
        ext = _TYPE_EXT.get(ctype) or mimetypes.guess_extension(ctype) or ".png"
        name = (stem or "image") + ext
    os.makedirs(folder, exist_ok=True)
    dest = unique_in(folder, name[:150])
    with open(dest, "wb") as f:
        f.write(data)
    return dest
