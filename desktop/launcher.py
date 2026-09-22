"""Desktop app shell: the packaged exe's entry point.

Starts the FastAPI backend (uvicorn, in-process, bound to 0.0.0.0 so phones
and other PCs on the LAN can still reach it) and opens it in a native
window via pywebview -- on Windows that's Microsoft Edge WebView2
(Chromium), so the Three.js 3D view renders exactly as it does in a normal
Edge/Chrome tab. Closing the window hides it to a system tray icon instead
of quitting, so other devices stay connected; "Quit" from the tray menu
stops the server and exits for real.

Needs a real display (pywebview/pystray) and is not covered by pytest --
see desktop/tests/test_server_lifecycle.py for the part of this that is
testable headless. Verify the window/tray behavior by hand on Windows.
"""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pystray  # noqa: E402
import webview  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from server_lifecycle import is_already_running, start_server, wait_until_ready  # noqa: E402

APP_NAME = "DMX Controller"
HOST = "0.0.0.0"
PORT = 8000


def _lan_addresses() -> list[str]:
    """Best-effort list of this machine's LAN IPs, for the tray tooltip --
    mirrors run.ps1's -Lan address listing so the packaged app tells the
    operator the same thing the dev script already did."""
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                addresses.add(ip)
    except OSError:
        pass
    return sorted(addresses)


def _tray_icon_image() -> Image.Image:
    """A plain generated icon -- no bundled asset needed. Swap for a real
    .ico via pystray.Icon(..., icon=Image.open(...)) if/when there's one."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(58, 123, 213, 255))
    draw.ellipse((size // 3, size // 3, size - size // 3, size - size // 3), fill=(255, 220, 100, 255))
    return img


def main() -> None:
    if is_already_running(HOST, PORT):
        # Already running (a previous launch, or another copy) -- never
        # start a second server fighting the first for the DMX port; just
        # open a window onto the one that's already up.
        webview.create_window(APP_NAME, f"http://127.0.0.1:{PORT}/index.html", width=1400, height=900)
        webview.start()
        return

    from app.main import app  # imported after sys.path setup above

    handle = start_server(app, host=HOST, port=PORT)
    if not wait_until_ready(HOST, PORT, timeout=15.0):
        handle.stop()
        raise RuntimeError(f"backend did not become ready on port {PORT}")

    window = webview.create_window(APP_NAME, f"http://127.0.0.1:{PORT}/index.html", width=1400, height=900)

    icon_holder: dict[str, pystray.Icon] = {}

    def on_closing() -> bool:
        """Hide instead of destroy: other devices (mobile, other PCs) may
        still be actively using this server."""
        window.hide()
        return False

    def show_window(icon=None, item=None) -> None:
        window.show()

    def quit_app(icon=None, item=None) -> None:
        tray = icon_holder.get("icon")
        if tray:
            tray.stop()
        handle.stop()
        window.destroy()

    window.events.closing += on_closing

    addresses = _lan_addresses()
    lan_line = ", ".join(f"http://{ip}:{PORT}" for ip in addresses) if addresses else "(no LAN address found)"
    tray = pystray.Icon(
        APP_NAME,
        _tray_icon_image(),
        f"{APP_NAME}\nLAN: {lan_line}",
        menu=pystray.Menu(
            pystray.MenuItem("Open", show_window, default=True),
            pystray.MenuItem("Quit", quit_app),
        ),
    )
    icon_holder["icon"] = tray
    threading.Thread(target=tray.run, daemon=True).start()

    webview.start()


if __name__ == "__main__":
    main()
