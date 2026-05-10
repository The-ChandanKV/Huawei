"""
AirPaste - Overlay UI v2
Optimized frameless transparent overlay with smooth animations.
"""

import tkinter as tk
import threading
import logging
from PIL import Image, ImageTk, ImageDraw

logger = logging.getLogger("AirPaste.Overlay")


class OverlayUI:
    STATUS_IDLE = "idle"
    STATUS_CAPTURED = "captured"
    STATUS_READY = "ready"
    STATUS_PASTED = "pasted"

    COLORS = {
        "bg": "#0A0E1A", "border": "#00F0FF", "accent_cyan": "#00F0FF",
        "accent_purple": "#A855F7", "success": "#10B981",
        "captured_bg": "#001A2C", "pasted_bg": "#0A1A0A",
        "text": "#E0F7FF",
    }

    STATUS_TEXT = {
        "idle": "", "captured": ">> SCREENSHOT CAPTURED",
        "ready": ">> READY TO PASTE", "pasted": ">> PASTED SUCCESSFULLY",
    }

    def __init__(self, config: dict):
        ov = config.get("overlay", {})
        self._pw = ov.get("preview_width", 200)
        self._ph = ov.get("preview_height", 150)
        self._fade_in_ms = ov.get("fade_in_ms", 300)
        self._fade_out_ms = ov.get("fade_out_ms", 250)
        self._display_ms = ov.get("display_duration_ms", 3500)
        self._position = ov.get("position", "bottom_right")
        self._max_opacity = ov.get("max_opacity", 0.92)

        self._ww = self._pw + 40
        self._wh = self._ph + 100

        self._root = None
        self._canvas = None
        self._opacity = 0.0
        self._visible = False
        self._panel_photo = None
        self._thumb_photo = None
        self._pending = []
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        if self._running: return
        self._running = True
        threading.Thread(target=self._run, daemon=True, name="OverlayThread").start()

    def _run(self):
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
            self._root = tk.Tk()
            self._root.title("AirPaste Overlay")
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            self._root.attributes("-alpha", 0.0)
            self._root.attributes("-transparentcolor", "#010101")
            self._root.configure(bg="#010101")
            self._position_window()

            self._canvas = tk.Canvas(self._root, width=self._ww, height=self._wh,
                                     bg="#010101", highlightthickness=0, bd=0)
            self._canvas.pack(fill=tk.BOTH, expand=True)
            self._draw_panel()
            self._set_click_through()
            self._root.after(50, self._process_pending)
            self._root.mainloop()
        except Exception as e:
            logger.error(f"Overlay error: {e}")
        finally:
            self._running = False

    def _set_click_through(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "AirPaste Overlay")
            if hwnd:
                GWL = -20
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL)
                style |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL, style)
        except Exception:
            pass

    def _position_window(self):
        sw, sh = self._root.winfo_screenwidth(), self._root.winfo_screenheight()
        m = 20
        pos = {
            "bottom_right": (sw - self._ww - m, sh - self._wh - m - 50),
            "bottom_left": (m, sh - self._wh - m - 50),
            "top_right": (sw - self._ww - m, m),
            "top_left": (m, m),
        }
        x, y = pos.get(self._position, pos["bottom_right"])
        self._root.geometry(f"{self._ww}x{self._wh}+{x}+{y}")

    def _draw_panel(self, thumbnail=None, status="idle"):
        if not self._canvas: return
        self._canvas.delete("all")

        colors = {
            "captured": (self.COLORS["accent_cyan"], self.COLORS["captured_bg"]),
            "pasted": (self.COLORS["success"], self.COLORS["pasted_bg"]),
            "ready": (self.COLORS["accent_purple"], self.COLORS["bg"]),
        }
        border, bg = colors.get(status, (self.COLORS["border"], self.COLORS["bg"]))

        img = Image.new("RGBA", (self._ww, self._wh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([2, 2, self._ww - 2, self._wh - 2], radius=14,
                             fill=bg + "E8", outline=border, width=2)
        self._panel_photo = ImageTk.PhotoImage(img)
        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._panel_photo)

        self._canvas.create_text(self._ww // 2, 16, text="<< AirPaste >>",
                                  fill=self.COLORS["accent_cyan"],
                                  font=("Consolas", 10, "bold"), anchor=tk.N)
        self._canvas.create_line(15, 34, self._ww - 15, 34, fill=border, width=1, dash=(3, 3))

        if thumbnail:
            try:
                t = thumbnail.copy().resize((self._pw, self._ph), Image.Resampling.LANCZOS)
                self._thumb_photo = ImageTk.PhotoImage(t)
                cx, cy = self._ww // 2, 40 + self._ph // 2
                self._canvas.create_image(cx, cy, anchor=tk.CENTER, image=self._thumb_photo)
                hw, hh = self._pw // 2 + 2, self._ph // 2 + 2
                self._canvas.create_rectangle(cx - hw, cy - hh, cx + hw, cy + hh,
                                               outline=border, width=1)
            except Exception:
                pass

        txt = self.STATUS_TEXT.get(status, "")
        if txt:
            self._canvas.create_text(self._ww // 2, self._wh - 22, text=txt,
                                      fill=border, font=("Consolas", 9, "bold"), anchor=tk.CENTER)

    def _process_pending(self):
        if not self._running: return
        with self._lock:
            calls = list(self._pending)
            self._pending.clear()
        for fn, a, kw in calls:
            try: fn(*a, **kw)
            except Exception: pass
        if self._root:
            self._root.after(50, self._process_pending)

    def _sched(self, fn, *a, **kw):
        with self._lock:
            self._pending.append((fn, a, kw))

    # --- Public thread-safe API ---

    def show_capture(self, thumb):
        self._sched(self._show_capture, thumb)

    def _show_capture(self, thumb):
        self._draw_panel(thumbnail=thumb, status="captured")
        self._fade_in()
        # Auto-dismiss after display duration — overlay should not stay forever
        if self._root:
            self._root.after(self._display_ms, self._fade_out)

    def show_pasted(self):
        self._sched(self._show_pasted)

    def _show_pasted(self):
        self._draw_panel(status="pasted")
        if not self._visible: self._fade_in()
        if self._root: self._root.after(self._display_ms, self._fade_out)

    def hide(self):
        self._sched(self._fade_out)

    def _fade_in(self):
        if not self._root: return
        steps, delay = 12, max(10, self._fade_in_ms // 12)
        inc = self._max_opacity / steps
        def step(i):
            if i >= steps or not self._running:
                self._visible = True; return
            o = min(self._max_opacity, inc * (i + 1))
            try: self._root.attributes("-alpha", o); self._opacity = o
            except tk.TclError: return
            self._root.after(delay, lambda: step(i + 1))
        step(0)

    def _fade_out(self):
        if not self._root or not self._visible: return
        steps, delay = 10, max(10, self._fade_out_ms // 10)
        start = self._opacity
        dec = start / steps
        def step(i):
            if i >= steps or not self._running:
                try: self._root.attributes("-alpha", 0.0)
                except tk.TclError: pass
                self._opacity = 0.0; self._visible = False; return
            o = max(0.0, start - dec * (i + 1))
            try: self._root.attributes("-alpha", o); self._opacity = o
            except tk.TclError: return
            self._root.after(delay, lambda: step(i + 1))
        step(0)

    def stop(self):
        self._running = False
        try:
            if self._root: self._root.after(0, self._root.destroy)
        except Exception: pass

    @property
    def is_visible(self): return self._visible
