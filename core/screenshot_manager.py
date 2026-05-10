"""
AirPaste - Optimized Screenshot Manager v2
Fast screen capture with mss, in-memory storage, thumbnail caching.
"""

import io
import time
import logging
from PIL import Image

logger = logging.getLogger("AirPaste.Screenshot")


class ScreenshotManager:
    def __init__(self, config: dict):
        ov = config.get("overlay", {})
        self._preview_size = (ov.get("preview_width", 200), ov.get("preview_height", 150))
        self._latest: Image.Image = None
        self._thumbnail: Image.Image = None
        self._count = 0
        self._last_hash = None
        logger.info(f"ScreenshotManager v2 init | preview={self._preview_size}")

    def capture(self) -> Image.Image:
        """Capture screen, detect duplicates, generate thumbnail."""
        try:
            import mss
            with mss.mss() as sct:
                mon = sct.monitors[1]
                t0 = time.perf_counter()
                raw = sct.grab(mon)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                ms = (time.perf_counter() - t0) * 1000

                # Quick duplicate check using corner pixel sampling
                sample = img.getpixel((10, 10)) + img.getpixel((img.width - 10, img.height - 10))
                if sample == self._last_hash:
                    logger.debug("Duplicate screenshot detected, skipping")
                    return self._latest
                self._last_hash = sample

                self._latest = img
                self._count += 1
                self._thumbnail = img.copy()
                self._thumbnail.thumbnail(self._preview_size, Image.Resampling.LANCZOS)
                logger.info(f"Screenshot #{self._count} | {img.size} | {ms:.1f}ms")
                return img
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return None

    @property
    def latest(self): return self._latest
    @property
    def thumbnail(self): return self._thumbnail
    @property
    def has_screenshot(self): return self._latest is not None
    @property
    def count(self): return self._count

    def get_bytes(self, fmt="BMP") -> bytes:
        if not self._latest: return None
        buf = io.BytesIO()
        self._latest.save(buf, format=fmt)
        return buf.getvalue()

    def clear(self):
        self._latest = None
        self._thumbnail = None
        self._last_hash = None
