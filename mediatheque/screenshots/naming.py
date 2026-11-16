"""File naming and shared page helpers for Article Screenshots.

Names follow the channel convention: NYT_Headline Words.png
"""
from __future__ import annotations

import glob
import html
import os
import re
import sys
from urllib.parse import urlparse

from ..config import data_dir

# Short names for filenames: NYT_Headline Words.png. Anything not listed falls back to
# the site's own name (og:site_name), then to its domain.
SITE_PREFIXES = {
    "nytimes.com": "NYT", "washingtonpost.com": "WaPo", "wsj.com": "WSJ", "theguardian.com": "Guardian",
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "cbc.ca": "CBC", "cnn.com": "CNN", "businessinsider.com": "BI",
    "time.com": "TIME", "thehill.com": "TheHill", "people.com": "People", "yahoo.com": "Yahoo",
    "nationalpost.com": "NationalPost", "forbes.com": "Forbes", "bloomberg.com": "Bloomberg",
    "reuters.com": "Reuters", "apnews.com": "AP", "npr.org": "NPR", "nbcnews.com": "NBC",
    "cbsnews.com": "CBS", "abcnews.go.com": "ABC", "foxnews.com": "Fox", "usatoday.com": "USAToday",
    "latimes.com": "LATimes", "theatlantic.com": "Atlantic", "newyorker.com": "NewYorker", "vox.com": "Vox",
    "theverge.com": "Verge", "wired.com": "Wired", "axios.com": "Axios", "politico.com": "Politico",
    "independent.co.uk": "Independent", "telegraph.co.uk": "Telegraph", "ft.com": "FT",
    "economist.com": "Economist", "x.com": "X", "twitter.com": "X", "reddit.com": "Reddit",
    "pubmed.ncbi.nlm.nih.gov": "PubMed", "entrepreneur.com": "Entrepreneur", "delish.com": "Delish",
}

# Title, site name and headline of the current page.
META_JS = r"""
() => {
  const meta = n => (document.querySelector(`meta[property="${n}"], meta[name="${n}"]`) || {}).content || '';
  const h1 = document.querySelector('h1');
  return {
    ogTitle: meta('og:title') || meta('twitter:title'),
    siteName: meta('og:site_name'),
    h1: h1 ? h1.innerText.trim() : '',
    title: document.title || '',
    words: (document.body.innerText || '').split(/\s+/).length,
    paywall: /subscribe|subscription|already a subscriber|log in to continue|sign in to continue/i.test(document.body.innerText || ''),
  };
}
"""


def own_browser() -> str | None:
    """On a Mac: Mediatheque's own browser (Playwright's "Google Chrome for Testing"), if it's
    installed. macOS sees it as a different app from Google Chrome, so having it open in the
    background (previews, the capture window) never stops your own Chrome from opening.
    Mediatheque.command installs it. None elsewhere, or if it isn't installed."""
    if sys.platform != "darwin":
        return None
    try:
        import json
        import playwright
        driver = os.path.join(os.path.dirname(playwright.__file__), "driver", "package", "browsers.json")
        with open(driver, encoding="utf-8") as f:
            revs = {b["name"]: b["revision"] for b in json.load(f)["browsers"]}
        cache = os.path.join(os.path.expanduser("~"), "Library", "Caches", "ms-playwright")
        shell = glob.glob(os.path.join(cache, f"chromium_headless_shell-{revs['chromium-headless-shell']}", "*"))
        app = glob.glob(os.path.join(cache, f"chromium-{revs['chromium']}", "chrome-mac*",
                                     "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"))
        return app[0] if app and shell else None  # previews need the headless one too
    except (OSError, ValueError, KeyError, ImportError):
        return None


def profile_dir() -> str:
    # Mediatheque's own browser on a Mac gets its own profile: it can't open one that a newer
    # Google Chrome has used.
    d = data_dir() / ("capture-profile" if own_browser() else "browser-profile")
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def site_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host == "web.archive.org":  # name archived pages after the original site
        inner = re.search(r"/web/\d+[a-z_]*/(https?://[^/]+)", url)
        if inner:
            host = (urlparse(inner.group(1)).hostname or host).lower()
    return host.removeprefix("www.")


def prefix_for(url: str, site_name: str = "") -> str:
    host = site_host(url)
    for domain, prefix in SITE_PREFIXES.items():
        if host == domain or host.endswith("." + domain):
            return prefix
    if site_name:
        name = re.sub(r"\.(com|org|net|co\.uk|ca|io|news)$", "", site_name.strip(), flags=re.I)
        name = re.sub(r",?\s+(LLC|Inc|Ltd|Limited|Corp|Co)\.?$", "", name, flags=re.I)
        name = " ".join(w[:1].upper() + w[1:] for w in name.split())
        cleaned = re.sub(r"[^A-Za-z0-9]", "", name)
        if 0 < len(cleaned) <= 24:
            return cleaned
    parts = host.split(".")
    core = parts[-2] if len(parts) >= 2 else host
    return core.upper() if len(core) <= 4 else core.capitalize()


def clean_headline(meta: dict, prefix: str = "") -> str:
    title = meta.get("ogTitle") or meta.get("h1") or meta.get("title") or ""
    title = re.sub(r"\s+", " ", html.unescape(title)).strip()
    site = (meta.get("siteName") or "").lower()
    keys = {k for k in (site, site.removeprefix("the "), prefix.lower()) if k}
    # Trailing "| Site", "- Site", "— Site", "on X"
    m = re.match(r"^(.*\S)\s*(?:[|\u2013\u2014-]|\bon)\s+([^|\u2013\u2014]{1,40})$", title)
    if m and any(k in m.group(2).lower() or m.group(2).lower() in k for k in keys):
        title = m.group(1).strip()
    return title


def file_name(prefix: str, headline: str, max_len: int = 120) -> str:
    text = re.sub(r'[\\/:*?"<>|“”]', "", headline)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    name = f"{prefix}_{text}" if text else prefix
    if len(name) > max_len:
        name = name[:max_len].rsplit(" ", 1)[0]
    return name.rstrip(" .")


def unique_path(folder: str, stem: str, ext: str = ".png") -> str:
    path, n = os.path.join(folder, stem + ext), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
    return path
