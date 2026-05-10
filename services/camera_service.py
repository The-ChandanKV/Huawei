"""
AirPaste - Ultra-Low Latency Camera Service v3
Lock-free latest-frame swap. Zero-copy where possible.
"""

import cv2
import time
import threading
import logging
import numpy as np

logger = logging.getLogger("AirPaste.Camera")


class CameraService:
    """
    Lock-free camera capture. Writes latest frame atomically.
    Reader always gets the most recent frame, never stale.
    """

    def __init__(self, config: dict):
        cam = config.get("camera", {})
        self._idx = cam.get("index", 0)
        self._cap_w = cam.get("capture_width", 640)
        self._cap_h = cam.get("capture_height", 480)
        self._inf_w = cam.get("inference_width", 256)
        self._inf_h = cam.get("inference_height", 192)
        self._reconnect_delay = cam.get("reconnect_delay_sec", 2.0)
        self._max_reconnect = cam.get("max_reconnect_attempts", 10)

        self._cap = None
        # Atomic frame slots - no lock needed for single-writer single-reader
        self._frame_bgr = None
        self._frame_rgb_small = None
        self._frame_id = 0
        self._running = False
        self._connected = False
        self._thread = None
        self._capture_ms = 0.0

        logger.info(f"CameraService v3 | cap={self._cap_w}x{self._cap_h} inf={self._inf_w}x{self._inf_h}")

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CamThread")
        self._thread.start()

    def _open(self) -> bool:
        try:
            self._cap = cv2.VideoCapture(self._idx, cv2.CAP_DSHOW)
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._cap_w)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._cap_h)
            self._cap.set(cv2.CAP_PROP_FPS, 30)
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimal buffer
            if self._cap.isOpened():
                self._cap.read()  # Warm up single frame
                self._connected = True
                logger.info("Camera opened")
                return True
            return False
        except Exception as e:
            logger.error(f"Camera open failed: {e}")
            return False

    def _loop(self):
        reconnects = 0
        # Pre-allocate resize buffer
        small_buf = np.empty((self._inf_h, self._inf_w, 3), dtype=np.uint8)

        while self._running:
            if not self._connected:
                if reconnects >= self._max_reconnect:
                    logger.error("Max reconnects reached")
                    break
                if not self._open():
                    reconnects += 1
                    time.sleep(self._reconnect_delay)
                    continue
                reconnects = 0

            t0 = time.perf_counter()

            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._connected = False
                self._release()
                continue

            # Flip + resize + color convert in minimal ops
            frame = cv2.flip(frame, 1)
            cv2.resize(frame, (self._inf_w, self._inf_h), dst=small_buf,
                       interpolation=cv2.INTER_NEAREST)
            rgb_small = cv2.cvtColor(small_buf, cv2.COLOR_BGR2RGB)

            # Atomic swap - single writer so no lock needed
            self._frame_bgr = frame
            self._frame_rgb_small = rgb_small
            self._frame_id += 1
            self._capture_ms = (time.perf_counter() - t0) * 1000

        self._release()

    def get_latest(self):
        """Returns (bgr_frame, rgb_small, frame_id) - always latest, never stale."""
        return self._frame_bgr, self._frame_rgb_small, self._frame_id

    @property
    def capture_ms(self):
        return self._capture_ms

    @property
    def is_connected(self):
        return self._connected

    @property
    def frame_count(self):
        return self._frame_id

    def _release(self):
        try:
            if self._cap and self._cap.isOpened():
                self._cap.release()
        except Exception:
            pass
        self._cap = None

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._release()
        self._connected = False
        logger.info("Camera stopped")
