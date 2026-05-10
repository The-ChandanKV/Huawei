"""
AirPaste - Paste Controller v3 (Native Win32 SendInput)
========================================================
Replaces pyautogui with direct Windows SendInput API.

Latency comparison:
  pyautogui.hotkey("ctrl","v")  ~110ms  (PAUSE=0.03 × 2 keys + 80ms sleep)
  win32 SendInput Ctrl+V        ~1-3ms  (direct kernel input queue injection)

SendInput fires Ctrl+Down + V+Down + V+Up + Ctrl+Up in a single syscall.
No delays, no Python overhead, no event loop roundtrips.
Falls back to pyautogui if SendInput unavailable.
"""

import ctypes
import ctypes.wintypes as wt
import time
import logging

logger = logging.getLogger("AirPaste.Paste")

# ─── Win32 Input Structures ───────────────────────────────────────────────────

INPUT_KEYBOARD   = 1
KEYEVENTF_KEYUP  = 0x0002
VK_CONTROL       = 0x11
VK_V             = 0x56

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          wt.LONG),
        ("dy",          wt.LONG),
        ("mouseData",   wt.DWORD),
        ("dwFlags",     wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wt.WORD),
        ("wScan",       wt.WORD),
        ("dwFlags",     wt.DWORD),
        ("time",        wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg",        wt.DWORD),
        ("wParamL",     wt.WORD),
        ("wParamH",     wt.WORD),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wt.DWORD),
        ("_",    _INPUT_UNION),
    ]

_extra = ctypes.pointer(ctypes.c_ulong(0))

def _make_key(vk: int, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type   = INPUT_KEYBOARD
    inp._.ki.wVk         = vk
    inp._.ki.wScan       = 0
    inp._.ki.dwFlags     = flags
    inp._.ki.time        = 0
    inp._.ki.dwExtraInfo = _extra
    return inp

# Pre-build the 4-event Ctrl+V sequence — allocated once at module load
_CTRL_V_SEQUENCE = (INPUT * 4)(
    _make_key(VK_CONTROL),               # Ctrl down
    _make_key(VK_V),                     # V   down
    _make_key(VK_V,       KEYEVENTF_KEYUP),   # V   up
    _make_key(VK_CONTROL, KEYEVENTF_KEYUP),   # Ctrl up
)
_INPUT_SIZE = ctypes.sizeof(INPUT)
_user32     = ctypes.windll.user32


def _send_ctrl_v_native() -> bool:
    """Single syscall: inject Ctrl+V key sequence via SendInput."""
    n = _user32.SendInput(4, _CTRL_V_SEQUENCE, _INPUT_SIZE)
    return n == 4


# ─── Controller ───────────────────────────────────────────────────────────────

class PasteController:
    """
    Ultra-fast Ctrl+V injection.

    Primary:  Win32 SendInput  — ~1-3ms, single syscall
    Fallback: pyautogui        — ~110ms, used only if SendInput fails
    """

    def __init__(self):
        self._count = 0
        # Verify SendInput works at init time
        try:
            _user32.SendInput(0, None, _INPUT_SIZE)
            self._use_native = True
            logger.info("PasteController v3 | Win32 SendInput ACTIVE (ultra-fast)")
        except Exception as e:
            self._use_native = False
            logger.warning(f"PasteController v3 | SendInput unavailable ({e}), using pyautogui fallback")

    def paste(self) -> bool:
        """
        Trigger Ctrl+V on the active window.
        No artificial delays. Target: <5ms total.
        """
        t0 = time.perf_counter()
        try:
            if self._use_native:
                ok = _send_ctrl_v_native()
            else:
                ok = self._pyautogui_fallback()

            ms = (time.perf_counter() - t0) * 1000
            if ok:
                self._count += 1
                logger.info(f"[Ctrl+V] #{self._count} sent in {ms:.2f}ms "
                            f"({'native' if self._use_native else 'pyautogui'})")
            else:
                logger.error(f"[Ctrl+V] SendInput returned 0 after {ms:.2f}ms")
            return ok

        except Exception as e:
            logger.error(f"paste() error: {e}")
            return self._pyautogui_fallback()

    def _pyautogui_fallback(self) -> bool:
        """Last-resort pyautogui path — only used if native fails."""
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE    = 0.0    # Remove all artificial delays
            pyautogui.hotkey("ctrl", "v")
            return True
        except Exception as e:
            logger.error(f"pyautogui fallback failed: {e}")
            return False

    @property
    def count(self) -> int:
        return self._count
