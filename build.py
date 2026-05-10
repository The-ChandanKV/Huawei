"""
AirPaste - PyInstaller Build Script
Run: python build.py
Produces: dist/AirPaste.exe
"""

import subprocess
import sys
import os

APP_ROOT = os.path.dirname(os.path.abspath(__file__))


def build():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",                    # No console window
        "--name", "AirPaste",
        "--icon", "NONE",
        "--add-data", f"config;config",  # Bundle config folder
        "--hidden-import", "mediapipe",
        "--hidden-import", "pystray",
        "--hidden-import", "cv2",
        "--hidden-import", "PIL",
        "--hidden-import", "win32clipboard",
        "--hidden-import", "keyboard",
        "--hidden-import", "mss",
        "--hidden-import", "pyautogui",
        "--hidden-import", "numpy",
        "--collect-data", "mediapipe",
        "--noconfirm",
        "--clean",
        "main.py",
    ]

    print("Building AirPaste.exe...")
    print(f"Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=APP_ROOT)

    if result.returncode == 0:
        exe_path = os.path.join(APP_ROOT, "dist", "AirPaste.exe")
        print(f"\nBuild successful!")
        print(f"Executable: {exe_path}")
        print(f"\nTo run: .\\dist\\AirPaste.exe")
        print(f"To add to startup: .\\dist\\AirPaste.exe --autostart")
    else:
        print(f"\nBuild failed with code {result.returncode}")
        sys.exit(1)


if __name__ == "__main__":
    build()
