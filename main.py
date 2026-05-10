"""
AirPaste v4.0 - Transition-Based Gesture Pipeline
===================================================
Architecture: 3-thread pipeline + transition event engine

  Thread 1: Camera capture  (lock-free latest-frame swap)
  Thread 2: Main loop       (gesture inference + transition detection)
  Thread 3: Action thread   (screenshot / paste, non-blocking)

Gesture UX:
  OPEN → CLOSED (quick fist)   → Screenshot INSTANTLY
  CLOSED → OPEN (open hand)    → Paste INSTANTLY

No holding. No waiting. Pure motion-driven interaction.
Target: <100ms perceived response.
"""

import sys
import os
import time
import threading
import logging
import queue
import ctypes
import ctypes.wintypes as wt

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from utils.config import load_config
config = load_config(APP_ROOT)

from utils.logger import setup_logging
setup_logging(config, APP_ROOT)
logger = logging.getLogger("AirPaste.Main")

import cv2
import keyboard
from core.gesture_detector import GestureDetector, GestureType, TransitionEvent
from core.screenshot_manager import ScreenshotManager
from core.clipboard_manager import ClipboardManager
from core.paste_controller import PasteController
from services.camera_service import CameraService
from services.tray_service import TrayService
from ui.overlay import OverlayUI


class ActionType:
    SCREENSHOT = "SCREENSHOT"
    PASTE      = "PASTE"


class AirPasteApp:
    """
    Transition-based gesture pipeline.

    Fires actions ONLY on gesture state transitions:
      OPEN_PALM  → CLOSED_FIST  : screenshot once
      CLOSED_FIST → OPEN_PALM  : paste once

    No holding, no repeated triggers, no lag.
    """

    def __init__(self):
        self.config = config
        self.running = False
        self.detection_active = True

        # Only a hard safety cooldown to prevent OS-level double-fire
        cd = config.get("cooldown", {})
        self._cd_shot  = 0.4   # seconds — hard floor, transition engine handles real debounce
        self._cd_paste = 0.4
        self._last_shot_t  = 0.0
        self._last_paste_t = 0.0

        # Modules
        logger.info("Initializing AirPaste v4.0 transition-based modules...")
        self.camera     = CameraService(config)
        self.gesture    = GestureDetector(config)
        self.screenshot = ScreenshotManager(config)
        self.clipboard  = ClipboardManager()
        self.paste_ctrl = PasteController()
        self.overlay    = OverlayUI(config)
        self.tray       = TrayService(on_toggle=self._on_toggle, on_exit=self._on_exit)

        # Action queue (non-blocking dispatch to separate thread)
        self._action_q      = queue.Queue(maxsize=4)
        self._action_thread = None

        # Screenshot availability flag
        self._has_screenshot = False

        # Profiling
        self._fps_count  = 0
        self._fps_time   = time.time()
        self._fps        = 0
        self._loop_ms    = 0.0
        self._last_frame_id = -1

        # Debug HUD
        app_cfg = config.get("app", {})
        self._debug = app_cfg.get("debug_mode", False)

        # Target window tracking — keeps the last non-AirPaste foreground window
        # so paste is delivered to the correct app, not the cv2 debug window
        self._target_hwnd  = None
        self._user32       = ctypes.windll.user32
        
        self._window_moved = False

        logger.info("All modules initialized — transition engine ready")

    # ─── Tray Callbacks ───

    def _on_toggle(self, active: bool):
        self.detection_active = active
        logger.info(f"Detection {'ACTIVE' if active else 'PAUSED'}")

    def _on_exit(self):
        logger.info("Tray exit requested")
        self.running = False

    # ─── Action Thread ───

    def _action_loop(self):
        """Dedicated thread for screenshot/paste so inference never blocks."""
        while self.running:
            try:
                action = self._action_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if action == ActionType.SCREENSHOT:
                    self._exec_screenshot()
                elif action == ActionType.PASTE:
                    self._exec_paste()
            except Exception as e:
                logger.error(f"Action error: {e}", exc_info=True)

    def _exec_screenshot(self):
        """
        Phase 1 of paste pipeline.
        Capture screen → encode DIB once → cache in RAM → write to clipboard.
        Everything is pre-prepared so paste later costs almost nothing.
        """
        t0 = time.perf_counter()

        t_cap = time.perf_counter()
        img = self.screenshot.capture()
        cap_ms = (time.perf_counter() - t_cap) * 1000

        if img is None:
            logger.error("Screenshot capture returned None")
            return

        t_clip = time.perf_counter()
        ok = self.clipboard.prepare_image(img)   # encode + cache DIB + write clipboard
        clip_ms = (time.perf_counter() - t_clip) * 1000

        total_ms = (time.perf_counter() - t0) * 1000
        if ok:
            logger.info(
                f"[SCREENSHOT] cap={cap_ms:.1f}ms  clip_prepare={clip_ms:.1f}ms  "
                f"total={total_ms:.1f}ms  | DIB cached & ready"
            )
            self._has_screenshot = True
            self._last_shot_t    = time.time()
            self.overlay.show_capture(self.screenshot.thumbnail)
        else:
            logger.error(f"Screenshot clipboard prepare failed after {total_ms:.1f}ms")

    def _exec_paste(self):
        """
        Phase 2 of paste pipeline — should feel INSTANT.

        Step A: inject_cached()  — re-write pre-cached DIB to clipboard (~1-3ms)
        Step B: paste_ctrl.paste() — SendInput Ctrl+V syscall (~1-3ms)

        Total target: <10ms end-to-end.
        """
        t0 = time.perf_counter()

        # Step A: activate target window so Ctrl+V lands in the right app
        if self._target_hwnd:
            try:
                self._user32.SetForegroundWindow(self._target_hwnd)
                time.sleep(0.018)   # ~18ms for OS to complete window activation
                logger.debug(f"Activated target HWND={self._target_hwnd}")
            except Exception as e:
                logger.warning(f"SetForegroundWindow failed: {e}")

        # Step B: re-inject pre-cached DIB (guarantees clipboard is fresh)
        t_inj = time.perf_counter()
        clip_ok = self.clipboard.inject_cached()
        inj_ms  = (time.perf_counter() - t_inj) * 1000

        if not clip_ok:
            logger.error(f"Clipboard inject failed after {inj_ms:.1f}ms — paste aborted")
            return

        # Step B: SendInput Ctrl+V (native Win32, single syscall)
        t_key  = time.perf_counter()
        key_ok = self.paste_ctrl.paste()
        key_ms = (time.perf_counter() - t_key) * 1000

        total_ms = (time.perf_counter() - t0) * 1000
        if key_ok:
            logger.info(
                f"[PASTE] inject={inj_ms:.1f}ms  ctrl_v={key_ms:.1f}ms  "
                f"total={total_ms:.1f}ms"
            )
            self._last_paste_t   = time.time()
            self._has_screenshot = False
            self.clipboard.clear_cache()
            self.overlay.show_pasted()
        else:
            logger.error(f"Ctrl+V send failed after {total_ms:.1f}ms")

    # ─── Target Window Tracking ───

    def _update_target_window(self):
        """
        Track the last foreground window that is NOT an AirPaste window.
        Called every frame so we always know where to deliver paste.
        """
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return
        buf = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        # Exclude our own windows (cv2 debug window and tkinter overlay)
        if "AirPaste" not in title:
            self._target_hwnd = hwnd

    def _queue_action(self, action: str):
        """Non-blocking enqueue. Drops if queue is full (avoids pileup)."""
        try:
            self._action_q.put_nowait(action)
        except queue.Full:
            logger.debug("Action queue full — skipped")

    # ─── Transition Handler ───

    def _on_transition(self, event: str):
        """
        Called ONCE per gesture state transition.
        This is the ONLY place actions are triggered by gestures.

          OPEN_TO_CLOSED → Screenshot
          CLOSED_TO_OPEN → Paste (only if screenshot ready)
        """
        now = time.time()

        if event == TransitionEvent.OPEN_TO_CLOSED:
            if (now - self._last_shot_t) >= self._cd_shot:
                logger.info(f"[TRANSITION] OPEN→CLOSED → Screenshot")
                self._queue_action(ActionType.SCREENSHOT)
            else:
                logger.debug("Screenshot cooldown active — skipped")

        elif event == TransitionEvent.CLOSED_TO_OPEN:
            if self._has_screenshot and (now - self._last_paste_t) >= self._cd_paste:
                logger.info(f"[TRANSITION] CLOSED→OPEN → Paste")
                self._queue_action(ActionType.PASTE)
            elif not self._has_screenshot:
                logger.debug("Paste skipped — no screenshot captured yet")
            else:
                logger.debug("Paste cooldown active — skipped")

    # ─── Hotkeys ───

    def _register_hotkeys(self):
        try:
            keyboard.add_hotkey("ctrl+shift+s", lambda: self._queue_action(ActionType.SCREENSHOT))
            keyboard.add_hotkey("ctrl+shift+v", lambda: self._queue_action(ActionType.PASTE))
            logger.info("Hotkeys registered: Ctrl+Shift+S / Ctrl+Shift+V")
        except Exception as e:
            logger.warning(f"Hotkey registration failed: {e}")

    # ─── Debug HUD ───

    def _draw_hud(self, frame, event: str):
        h, w = frame.shape[:2]
        ov = frame.copy()
        cv2.rectangle(ov, (0, 0), (w, 58), (10, 14, 26), -1)
        cv2.addWeighted(ov, 0.75, frame, 0.25, 0, frame)

        # Row 1
        cv2.putText(frame, "AirPaste v4 [TRANSITION]", (8, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 240, 255), 1)

        state_color = {
            GestureType.CLOSED_FIST: (0, 100, 255),
            GestureType.OPEN_PALM:   (0, 220, 60),
            GestureType.NONE:        (140, 140, 140),
        }.get(self.gesture.confirmed_state, (200, 200, 200))

        cv2.putText(frame, f"State: {self.gesture.confirmed_state}", (240, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, state_color, 1)

        status_str = "ACTIVE" if self.detection_active else "PAUSED"
        sc = (0, 255, 0) if self.detection_active else (0, 0, 255)
        cv2.putText(frame, status_str, (w - 75, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, sc, 1)

        # Row 2
        cv2.putText(frame, f"FPS:{self._fps}", (8, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 0), 1)
        cv2.putText(frame, f"Infer:{self.gesture.inference_ms:.0f}ms", (72, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 200, 0), 1)
        cv2.putText(frame, f"Loop:{self._loop_ms:.0f}ms", (190, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
        cv2.putText(frame, f"Cam:{self.camera.capture_ms:.0f}ms", (290, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
        cv2.putText(frame, f"Shots:{self.screenshot.count}", (w - 80, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (126, 200, 227), 1)

        # Row 3 — last event flash
        if event != TransitionEvent.NONE:
            ev_color = (0, 255, 128) if event == TransitionEvent.OPEN_TO_CLOSED else (0, 180, 255)
            cv2.putText(frame, f">> {event} <<", (8, 55),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, ev_color, 1)

        return frame

    # ─── Main Loop ───

    def run(self):
        print("""
  +================================================+
  |         AirPaste v4.0 — TRANSITION ENGINE       |
  |  [Fist] = Screenshot   [Open] = Paste  [Q]=Quit |
  |  Quick hand motion — instant response, no hold   |
  +================================================+
""")
        logger.info("Starting AirPaste v4.0 transition-based pipeline...")

        # Boot services
        self.overlay.start()
        time.sleep(0.2)
        self.tray.start()
        self.camera.start()
        time.sleep(0.8)      # allow camera to warm up
        self._register_hotkeys()

        # Start action thread
        self.running = True
        self._action_thread = threading.Thread(
            target=self._action_loop, daemon=True, name="ActionThread"
        )
        self._action_thread.start()

        logger.info("Pipeline running — waiting for gesture transitions")

        _last_event = TransitionEvent.NONE   # carry for HUD flash

        try:
            while self.running:
                loop_t0 = time.perf_counter()

                # Pull latest camera frame (lock-free)
                bgr, rgb_small, fid = self.camera.get_latest()
                if bgr is None or rgb_small is None:
                    time.sleep(0.005)
                    continue

                # Skip identical frame — camera hasn't produced a new one yet
                if fid == self._last_frame_id:
                    time.sleep(0.001)
                    continue
                self._last_frame_id = fid

                # ── Track target window (where paste should land) ──
                self._update_target_window()

                # ── Gesture inference + transition detection ──
                event = TransitionEvent.NONE
                results = None

                if self.detection_active:
                    _raw, event, results = self.gesture.detect(rgb_small)
                    if event != TransitionEvent.NONE:
                        _last_event = event
                        self._on_transition(event)

                # ── Debug display ──
                if self._debug:
                    display = bgr.copy()
                    if results:
                        display = self.gesture.draw_landmarks(display, results)
                    display = self._draw_hud(display, _last_event)
                    cv2.imshow("AirPaste v4 - Debug", display)
                    if not self._window_moved:
                        cv2.moveWindow("AirPaste v4 - Debug", 20, 20)
                        self._window_moved = True
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    # Fade event label after a few frames
                    if _last_event != TransitionEvent.NONE and event == TransitionEvent.NONE:
                        _last_event = TransitionEvent.NONE

                # ── FPS tracking ──
                self._fps_count += 1
                now = time.time()
                if now - self._fps_time >= 1.0:
                    self._fps      = self._fps_count
                    self._fps_count = 0
                    self._fps_time  = now
                    if self._debug:
                        logger.debug(
                            f"FPS={self._fps} | infer={self.gesture.inference_ms:.1f}ms "
                            f"cam={self.camera.capture_ms:.1f}ms loop={self._loop_ms:.1f}ms"
                        )

                self._loop_ms = (time.perf_counter() - loop_t0) * 1000

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
        finally:
            self._shutdown()

    def _shutdown(self):
        logger.info("Shutting down AirPaste v4.0...")
        self.running = False
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self.camera.stop()
        self.gesture.release()
        self.overlay.stop()
        self.tray.stop()
        cv2.destroyAllWindows()
        logger.info("Shutdown complete")


def main():
    if "--autostart" in sys.argv:
        _setup_autostart(True)
        return
    if "--no-autostart" in sys.argv:
        _setup_autostart(False)
        return
    app = AirPasteApp()
    app.run()


def _setup_autostart(enable: bool):
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        if enable:
            path = f'"{sys.executable}" "{os.path.join(APP_ROOT, "main.py")}"'
            winreg.SetValueEx(key, "AirPaste", 0, winreg.REG_SZ, path)
            print("Added to Windows startup")
        else:
            try:
                winreg.DeleteValue(key, "AirPaste")
            except FileNotFoundError:
                pass
            print("Removed from Windows startup")
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Autostart error: {e}")


if __name__ == "__main__":
    main()
