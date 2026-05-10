"""
AirPaste - Configuration Manager
Loads, validates, and provides access to application settings.
"""

import json
import os
import logging

logger = logging.getLogger("AirPaste.Config")

_DEFAULT_CONFIG = {
    "app": {"name": "AirPaste", "version": "2.0.0", "debug_mode": False,
            "start_minimized": True, "auto_start_detection": True,
            "sound_feedback": False, "startup_with_windows": False},
    "camera": {"index": 0, "capture_width": 640, "capture_height": 480,
               "inference_width": 320, "inference_height": 240,
               "target_fps": 30, "reconnect_delay_sec": 3.0,
               "max_reconnect_attempts": 10},
    "gesture": {"fist_threshold": 0.25, "palm_threshold": 0.75,
                "stability_frames": 4, "min_detection_confidence": 0.65,
                "min_tracking_confidence": 0.55, "landmark_smoothing_alpha": 0.4,
                "partial_hand_min_landmarks": 15, "max_hand_distance_ratio": 0.85},
    "cooldown": {"screenshot_sec": 2.5, "paste_sec": 2.5,
                 "adaptive_enabled": True, "adaptive_min_sec": 1.5,
                 "adaptive_max_sec": 5.0},
    "overlay": {"preview_width": 200, "preview_height": 150,
                "fade_in_ms": 300, "fade_out_ms": 250,
                "display_duration_ms": 3500, "position": "bottom_right",
                "max_opacity": 0.92},
    "performance": {"frame_skip_enabled": True, "process_every_n_frames": 2,
                    "camera_buffer_size": 2, "max_queue_size": 3},
    "logging": {"console_level": "INFO", "file_level": "DEBUG",
                "max_file_size_mb": 5, "backup_count": 3},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning merged dict."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(app_root: str) -> dict:
    """Load config from config/settings.json, merged with defaults."""
    config_path = os.path.join(app_root, "config", "settings.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        config = _deep_merge(_DEFAULT_CONFIG, user_cfg)
        logger.info(f"Config loaded from {config_path}")
    except FileNotFoundError:
        logger.warning("Config file not found, using defaults")
        config = _DEFAULT_CONFIG.copy()
    except json.JSONDecodeError as e:
        logger.error(f"Config parse error: {e}, using defaults")
        config = _DEFAULT_CONFIG.copy()
    return config


def get_app_root() -> str:
    """Get the application root directory."""
    if getattr(sys, "frozen", False):
        import sys
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Convenience import
import sys
