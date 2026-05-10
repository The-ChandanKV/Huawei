# AirPaste v2.0 - Gesture-Controlled Screenshot Tool

A production-grade Windows desktop application that captures screenshots and pastes them anywhere using hand gestures through a webcam. Runs as a background service with system tray integration.

---

## Features

| Feature | Description |
|---------|-------------|
| Closed Fist | Captures screenshot + copies to clipboard |
| Open Palm | Pastes into any focused application |
| System Tray | Runs silently in background, toggle on/off |
| Threaded Camera | Dedicated capture thread for zero-lag frames |
| Frame Skipping | Processes every Nth frame for lower CPU usage |
| Landmark Smoothing | EMA filter reduces jitter and false triggers |
| Adaptive Cooldown | Cooldown scales with usage frequency |
| Auto-Reconnect | Camera recovery on disconnect |
| File Logging | Rotating logs for runtime and errors |
| Keyboard Fallback | Ctrl+Shift+S / Ctrl+Shift+V shortcuts |
| PyInstaller Ready | Build standalone .exe for deployment |

---

## Project Structure

```
AirPaste/
├── main.py                          # Application entry point
├── build.py                         # PyInstaller build script
├── requirements.txt                 # Python dependencies
├── README.md
├── config/
│   └── settings.json                # All tunable settings
├── core/
│   ├── gesture_detector.py          # MediaPipe gesture recognition
│   ├── screenshot_manager.py        # Screen capture (mss)
│   ├── clipboard_manager.py         # Windows clipboard (pywin32)
│   └── paste_controller.py          # Ctrl+V simulation
├── services/
│   ├── camera_service.py            # Threaded webcam capture
│   └── tray_service.py              # System tray icon (pystray)
├── ui/
│   └── overlay.py                   # Floating transparent overlay
├── utils/
│   ├── logger.py                    # Rotating file + console logging
│   └── config.py                    # Config loader with defaults
└── logs/
    ├── runtime.log                  # All activity logs
    └── errors.log                   # Errors only
```

---

## Installation

### Prerequisites
- Python 3.9+ (tested on 3.10)
- Windows 10/11
- Webcam

### Setup
```bash
cd e:\Project\Huawei\AirPaste
pip install -r requirements.txt
```

### Run
```bash
python main.py
```

### Build Standalone .exe
```bash
python build.py
# Output: dist/AirPaste.exe
```

### Add to Windows Startup
```bash
python main.py --autostart
# Remove: python main.py --no-autostart
```

---

## Controls

| Input | Action |
|-------|--------|
| Closed Fist | Capture screenshot |
| Open Palm | Paste screenshot |
| Ctrl+Shift+S | Manual screenshot |
| Ctrl+Shift+V | Manual paste |
| System Tray > Pause | Toggle detection |
| System Tray > Exit | Quit application |
| Q / ESC | Quit (debug window only) |

---

## Configuration (config/settings.json)

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| app | debug_mode | false | Show webcam window + verbose logs |
| camera | inference_width | 320 | Smaller = faster inference |
| gesture | stability_frames | 4 | Frames needed for stable detection |
| gesture | landmark_smoothing_alpha | 0.4 | EMA smoothing (0=smooth, 1=responsive) |
| cooldown | screenshot_sec | 2.5 | Minimum seconds between captures |
| cooldown | adaptive_enabled | true | Auto-adjust cooldown with usage |
| performance | process_every_n_frames | 2 | Skip frames for lower CPU |

---

## Architecture

```
Camera Thread ──> Frame Buffer ──> Main Loop ──> Gesture Detector
                                       │              │
                                       │         [FIST] ──> Screenshot ──> Clipboard ──> Overlay
                                       │         [PALM] ──> Paste Ctrl+V ──> Overlay
                                       │
                                  Tray Service (background)
                                  Hotkey Listener
```

**Key optimizations:**
- Camera runs in dedicated thread (no main loop blocking)
- Frames resized to 320x240 for inference (50% less pixels)
- EMA landmark smoothing eliminates jitter
- Frame skipping reduces CPU by ~50%
- Edge-triggered gestures prevent repeated actions
- Adaptive cooldown prevents spam

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| opencv-python | >=4.8 | Webcam handling |
| mediapipe | 0.10.14 | Hand tracking |
| mss | >=9.0 | Screen capture |
| pywin32 | >=306 | Clipboard |
| pyautogui | >=0.9 | Key simulation |
| Pillow | >=10.0 | Image processing |
| keyboard | >=0.13 | Global hotkeys |
| numpy | >=1.24,<2 | Numerics |
| pystray | >=0.19 | System tray |
| pyinstaller | >=6.0 | Exe building |
