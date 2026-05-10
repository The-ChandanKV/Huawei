"""
AirPaste - Clipboard Manager v3 (Pre-cache Architecture)
=========================================================
Two-phase design for zero-latency paste:

  Phase 1 (at screenshot time):
    prepare_image() → encode PIL → cache raw DIB bytes in RAM
    → write to Windows clipboard immediately

  Phase 2 (at paste time):
    inject_cached() → write pre-cached DIB bytes to clipboard
    → NO encoding, NO conversion — pure memory write (~1-3ms)

Critical: clipboard is re-injected at paste time to guarantee
freshness even if another app has touched it between capture and paste.
"""

import io
import time
import logging
import win32clipboard
import win32con
from PIL import Image

logger = logging.getLogger("AirPaste.Clipboard")


class ClipboardManager:
    """
    Ultra-fast clipboard manager with pre-cached DIB.

    Workflow:
      1. prepare_image(img)   → encode BMP once, cache DIB bytes, write to clipboard
      2. inject_cached()      → re-write cached DIB to clipboard (no encode, ~1ms)

    The expensive PIL→BMP encode happens ONCE at screenshot time.
    Paste time cost: only OpenClipboard + SetClipboardData + CloseClipboard.
    """

    def __init__(self):
        self._cached_dib: bytes | None = None   # Pre-encoded DIB, ready to inject
        self._cached_size: tuple = (0, 0)        # For debug logging
        self._is_open = False
        logger.info("ClipboardManager v3 [pre-cache] init")

    # ─── Internal clipboard helpers ───

    def _open_clipboard(self, retries: int = 3) -> bool:
        """Open clipboard with brief retry for transient locks."""
        for attempt in range(retries):
            try:
                win32clipboard.OpenClipboard()
                self._is_open = True
                return True
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.005)   # 5ms retry — clipboard locked by another app
        return False

    def _close_clipboard(self):
        if self._is_open:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass
            self._is_open = False

    def _write_dib(self, dib_bytes: bytes) -> bool:
        """Write raw DIB bytes to clipboard. Assumes clipboard is already open."""
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib_bytes)
            return True
        except Exception as e:
            logger.error(f"SetClipboardData failed: {e}")
            return False

    # ─── Public API ───

    def prepare_image(self, image: Image.Image) -> bool:
        """
        Phase 1 — called immediately after screenshot capture.

        Converts PIL image to DIB bytes ONCE and caches them.
        Also writes to clipboard immediately so it's ready.

        Returns True if successful.
        """
        if image is None:
            return False

        t0 = time.perf_counter()

        try:
            # Ensure RGB (fastest encode path)
            if image.mode != "RGB":
                image = image.convert("RGB")

            # Encode to BMP in memory — strip 14-byte file header to get DIB
            buf = io.BytesIO()
            image.save(buf, format="BMP")
            dib_bytes = buf.getvalue()[14:]
            encode_ms = (time.perf_counter() - t0) * 1000

            # Cache for instant re-inject at paste time
            self._cached_dib  = dib_bytes
            self._cached_size = image.size

            # Write to clipboard immediately
            t1 = time.perf_counter()
            if self._open_clipboard():
                ok = self._write_dib(dib_bytes)
                self._close_clipboard()
                write_ms = (time.perf_counter() - t1) * 1000
                logger.info(
                    f"Clipboard prepared | {image.size} | "
                    f"encode={encode_ms:.1f}ms write={write_ms:.1f}ms "
                    f"total={(time.perf_counter()-t0)*1000:.1f}ms"
                )
                return ok
            else:
                logger.error("Could not open clipboard for initial write")
                return False

        except Exception as e:
            logger.error(f"prepare_image failed: {e}")
            self._close_clipboard()
            return False

    def inject_cached(self) -> bool:
        """
        Phase 2 — called at paste time.

        Re-writes pre-cached DIB bytes to clipboard.
        No image encoding. No conversion. Pure memory write.
        Target: ~1-3ms total.

        Returns True if successful.
        """
        if self._cached_dib is None:
            logger.warning("inject_cached: no cached DIB — call prepare_image first")
            return False

        t0 = time.perf_counter()
        try:
            if self._open_clipboard():
                ok = self._write_dib(self._cached_dib)
                self._close_clipboard()
                ms = (time.perf_counter() - t0) * 1000
                logger.info(f"Clipboard injected | {self._cached_size} | {ms:.1f}ms")
                return ok
            else:
                logger.error("inject_cached: could not open clipboard")
                return False
        except Exception as e:
            logger.error(f"inject_cached failed: {e}")
            self._close_clipboard()
            return False

    def copy_image(self, image: Image.Image) -> bool:
        """Convenience: prepare + inject in one call (used by hotkey path)."""
        return self.prepare_image(image)

    @property
    def has_cached(self) -> bool:
        return self._cached_dib is not None

    def clear_cache(self):
        self._cached_dib = None
        self._cached_size = (0, 0)

    def has_image(self) -> bool:
        try:
            if self._open_clipboard():
                r = win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB)
                self._close_clipboard()
                return bool(r)
            return False
        except Exception:
            self._close_clipboard()
            return False

    def clear(self):
        try:
            if self._open_clipboard():
                win32clipboard.EmptyClipboard()
                self._close_clipboard()
        except Exception:
            self._close_clipboard()
        self.clear_cache()
