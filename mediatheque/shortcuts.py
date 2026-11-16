"""Create Start menu and desktop shortcuts for Mediatheque.

Run again after moving the project folder:  .venv\\Scripts\\python -m mediatheque.shortcuts
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QGuiApplication

if sys.platform != "win32":
    sys.exit("Start menu and desktop shortcuts are for Windows. On a Mac, double-click Mediatheque.command "
             "(or drag it to the Dock).")

ROOT = Path(__file__).resolve().parent.parent
ICON_PATH = ROOT / "mediatheque" / "assets" / "mediatheque.ico"


def write_icon(path: Path) -> Path:
    """Save the pixel-art app icon as a multi-size .ico (PNG-compressed entries)."""
    from .ui.theme import make_icon

    sizes = (16, 24, 32, 48, 64, 128, 256)
    icon = make_icon()
    images = []
    for s in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        icon.pixmap(QSize(s, s)).scaled(s, s).save(buf, "PNG")
        images.append(bytes(data))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for s, png in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + b"".join(images))
    return path


def create_shortcuts() -> list[str]:
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    ps = r"""
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Mediatheque.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Mediatheque.lnk')
)
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $env:MT_PYTHONW
    $lnk.Arguments = '-m mediatheque'
    $lnk.WorkingDirectory = $env:MT_ROOT
    $lnk.IconLocation = "$env:MT_ICON,0"
    $lnk.Description = 'Download YouTube, TikTok and Instagram videos'
    $lnk.Save()
    Write-Output $path
}
"""
    env = {**os.environ, "MT_PYTHONW": str(pythonw), "MT_ROOT": str(ROOT), "MT_ICON": str(ICON_PATH)}
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         capture_output=True, text=True, env=env, check=True)
    return out.stdout.split()


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    print("Icon:", write_icon(ICON_PATH))
    for p in create_shortcuts():
        print("Shortcut:", p)
