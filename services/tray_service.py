"""
AirPaste - System Tray Service
Background tray icon with Start/Stop/Exit controls using pystray.
"""

import threading
import logging
import os
from PIL import Image, ImageDraw

logger = logging.getLogger("AirPaste.Tray")


def _create_icon_image(color="#00F0FF", size=64):
    """Generate a simple tray icon programmatically."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Outer circle
    draw.ellipse([4, 4, size - 4, size - 4], fill=color, outline="#FFFFFF", width=2)
    # Inner "A" letter
    cx, cy = size // 2, size // 2
    draw.text((cx - 8, cy - 10), "AP", fill="#0A0E1A")
    return img


class TrayService:
    """
    System tray icon service providing:
    - Start/Stop detection toggle
    - Exit application
    - Status indicator via icon color
    """

    def __init__(self, on_toggle=None, on_exit=None):
        self._on_toggle = on_toggle
        self._on_exit = on_exit
        self._icon = None
        self._thread = None
        self._running = False
        self._detection_active = True

    def start(self):
        """Start tray icon in background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="TrayThread")
        self._thread.start()
        logger.info("TrayService started")

    def _run(self):
        """Create and run the system tray icon."""
        try:
            import pystray
            from pystray import MenuItem, Menu

            def on_toggle(icon, item):
                self._detection_active = not self._detection_active
                status = "Active" if self._detection_active else "Paused"
                logger.info(f"Detection toggled: {status}")
                if self._on_toggle:
                    self._on_toggle(self._detection_active)
                # Update icon color
                icon.icon = _create_icon_image(
                    "#00F0FF" if self._detection_active else "#FF6B6B"
                )

            def on_exit(icon, item):
                logger.info("Exit requested from tray")
                icon.stop()
                if self._on_exit:
                    self._on_exit()

            def toggle_text(item):
                return "Pause Detection" if self._detection_active else "Resume Detection"

            menu = Menu(
                MenuItem(toggle_text, on_toggle),
                Menu.SEPARATOR,
                MenuItem("Exit AirPaste", on_exit),
            )

            self._icon = pystray.Icon(
                "AirPaste",
                icon=_create_icon_image(),
                title="AirPaste - Gesture Screenshot Tool",
                menu=menu,
            )
            self._icon.run()
        except ImportError:
            logger.warning("pystray not installed, tray icon disabled")
        except Exception as e:
            logger.error(f"Tray error: {e}")

    def update_status(self, active: bool):
        """Update tray icon color based on detection status."""
        self._detection_active = active
        if self._icon:
            try:
                self._icon.icon = _create_icon_image(
                    "#00F0FF" if active else "#FF6B6B"
                )
            except Exception:
                pass

    def stop(self):
        """Stop the tray icon."""
        self._running = False
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
        logger.info("TrayService stopped")

    @property
    def detection_active(self):
        return self._detection_active
