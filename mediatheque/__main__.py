import logging
import os
import sys


def _python_fixes() -> None:
    """Work around things that differ on a fresh python.org install (the Mac's)."""
    # Python 3.15 loads concurrent.futures' pool classes lazily, and yt-dlp first touches them from a
    # download thread, where they come back unusable ("'lazy_import' object is not callable").
    # Loading them here, on the main thread, first avoids that. Harmless on older Pythons.
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor  # noqa: F401
    # python.org's Mac installer has no web certificates until "Install Certificates.command" is run,
    # so previews and other downloads fail with CERTIFICATE_VERIFY_FAILED. Use certifi's instead.
    if sys.platform == "darwin" and not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            pass


def main() -> int:
    _python_fixes()
    # pythonw.exe has no console; give libraries somewhere harmless to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    # Exact fractional scaling (150%, 175%, 225%…) so the UI is crisp on 4K.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    from .osutil import set_mac_app_name
    set_mac_app_name("Mediatheque")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mediatheque.App")

    from .config import Settings, setup_logging
    from .platforms import PLATFORMS
    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme, make_icon

    setup_logging([p.key for p in PLATFORMS] + ["screenshots"])
    log = logging.getLogger("mediatheque")

    app = QApplication(sys.argv)
    app.setApplicationName("Mediatheque")
    app.setWindowIcon(make_icon())
    apply_theme(app)

    def excepthook(exc_type, exc, tb):
        log.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(None, "Mediatheque", f"Something went wrong:\n\n{exc}")

    sys.excepthook = excepthook

    from .transfer import import_transfer
    try:
        imported = import_transfer()  # projects brought over with Tools > Pack for Another Computer
    except OSError:
        log.exception("Couldn't import the transfer folder")
        imported = 0

    window = MainWindow(Settings())
    window.show()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(800, window.offer_tutorial)  # first launch: "New to Mediatheque? Take the tour"
    if imported:
        window.show_status(f"Brought in {imported} script project{'s' if imported != 1 else ''} from the "
                           "other computer. Open one from the Script tool's dropdown.", 20000)
    return app.exec()


def _run() -> int:
    try:
        return main()
    except Exception as e:
        logging.getLogger("mediatheque").critical("Mediatheque couldn't start", exc_info=True)
        # Started from Mediatheque.app there's no Terminal to show the error: say so on screen.
        if sys.platform == "darwin" and not (sys.stderr and sys.stderr.isatty()):
            import subprocess
            msg = str(e).replace('"', "'")[:300]
            subprocess.run(["osascript", "-e", f'display alert "Mediatheque couldn\'t start" message "{msg}\n\n'
                            'The details are in Application Support > Mediatheque > logs." as critical'])
        raise


if __name__ == "__main__":
    sys.exit(_run())
