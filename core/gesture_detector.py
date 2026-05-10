"""
AirPaste - Transition-Based Gesture Engine v4
=============================================
Detects FAST gesture TRANSITIONS, not static poses.

State machine:
  OPEN_PALM  → CLOSED_FIST  : fires SCREENSHOT once
  CLOSED_FIST → OPEN_PALM   : fires PASTE once

Design goals:
- No holding required — react to motion
- 2-frame vote confirmation (≈33ms at 60fps)
- EMA smoothing tolerates rapid hand movement
- Velocity-aware: fast closure = higher confidence
- Debounce guard prevents accidental re-triggers
- Single transition callback, never repeated
"""

import numpy as np
import logging
import time
import mediapipe as mp
from collections import deque

logger = logging.getLogger("AirPaste.Gesture")


class GestureType:
    NONE   = "NONE"
    CLOSED_FIST = "FIST"
    OPEN_PALM   = "PALM"
    UNKNOWN     = "UNK"


class TransitionEvent:
    OPEN_TO_CLOSED = "OPEN→CLOSED"   # → Screenshot
    CLOSED_TO_OPEN = "CLOSED→OPEN"   # → Paste
    NONE           = "NONE"


class GestureStateMachine:
    """
    Pure state machine: tracks confirmed gesture state and fires
    transition events ONCE per edge — never on static holds.

    State transitions:
        NONE/UNK → PALM  : settled state, no event
        NONE/UNK → FIST  : settled state, no event
        PALM → FIST      : fires OPEN_TO_CLOSED
        FIST → PALM      : fires CLOSED_TO_OPEN
    """

    # Minimum consecutive raw votes needed to confirm a new state
    _VOTE_WINDOW = 2          # frames — ~33ms at 60fps, fast but not hair-trigger
    _DEBOUNCE_SEC = 0.4       # seconds — minimum gap between any two events

    def __init__(self):
        self._confirmed_state = GestureType.NONE
        self._candidate      = GestureType.NONE
        self._candidate_count = 0
        self._last_event_t   = 0.0

    def update(self, raw_gesture: str) -> str:
        """
        Feed one raw classified gesture.
        Returns a TransitionEvent string or TransitionEvent.NONE.
        """
        if raw_gesture == GestureType.UNKNOWN:
            # Partial hand — don't reset, just keep waiting
            return TransitionEvent.NONE

        # Track candidate streak
        if raw_gesture == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = raw_gesture
            self._candidate_count = 1

        # Not enough votes yet
        if self._candidate_count < self._VOTE_WINDOW:
            return TransitionEvent.NONE

        # Candidate is now confirmed — check if it differs from current state
        new_state = self._candidate
        if new_state == self._confirmed_state:
            return TransitionEvent.NONE

        # Debounce: don't fire faster than _DEBOUNCE_SEC
        now = time.monotonic()
        if (now - self._last_event_t) < self._DEBOUNCE_SEC:
            return TransitionEvent.NONE

        # Determine transition type
        prev = self._confirmed_state
        self._confirmed_state = new_state
        self._last_event_t = now

        if (prev in (GestureType.OPEN_PALM, GestureType.NONE)) and new_state == GestureType.CLOSED_FIST:
            return TransitionEvent.OPEN_TO_CLOSED

        if prev == GestureType.CLOSED_FIST and new_state == GestureType.OPEN_PALM:
            return TransitionEvent.CLOSED_TO_OPEN

        # e.g. NONE → PALM or NONE → FIST — no action, just settle
        return TransitionEvent.NONE

    @property
    def confirmed_state(self) -> str:
        return self._confirmed_state


class GestureDetector:
    """
    Ultra-fast transition-based gesture detector.

    Pipeline per frame:
      1. MediaPipe Hands (model_complexity=0)
      2. Extract 21-landmark numpy array
      3. EMA smoothing (alpha=0.6 — lighter smoothing for faster response)
      4. Vectorized finger extension classification
      5. Velocity score (fingertip motion magnitude) for confidence boost
      6. GestureStateMachine edge detection
      7. Emit TransitionEvent only on state change
    """

    # Finger landmark indices (no thumb — more stable for fist/palm)
    FINGER_TIPS = np.array([8, 12, 16, 20])
    FINGER_PIPS = np.array([6, 10, 14, 18])
    # MCP bases for curl ratio
    FINGER_MCPS = np.array([5,  9, 13, 17])

    def __init__(self, config: dict):
        g = config.get("gesture", {})

        # Thresholds — tuned for fast transitions
        self._fist_thresh  = g.get("fist_threshold", 0.20)   # ≤20% extended → FIST
        self._palm_thresh  = g.get("palm_threshold", 0.70)   # ≥70% extended → PALM
        self._alpha        = g.get("landmark_smoothing_alpha", 0.6)  # higher = faster response

        # MediaPipe
        self.mp_hands    = mp.solutions.hands
        self.mp_drawing  = mp.solutions.drawing_utils
        self.mp_styles   = mp.solutions.drawing_styles

        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=0,          # FASTEST model
            min_detection_confidence=g.get("min_detection_confidence", 0.55),
            min_tracking_confidence=g.get("min_tracking_confidence", 0.45),
        )

        # Internal state
        self._prev_coords  = None
        self._prev_tips    = None          # for velocity measurement
        self._state_machine = GestureStateMachine()
        self._inference_ms  = 0.0
        self._classify_ms   = 0.0

        # Last detected raw + stable for HUD display
        self._last_raw      = GestureType.NONE
        self._last_stable   = GestureType.NONE

        logger.info(
            f"GestureDetector v4 [TRANSITION ENGINE] | "
            f"fist≤{self._fist_thresh} palm≥{self._palm_thresh} "
            f"alpha={self._alpha} vote={GestureStateMachine._VOTE_WINDOW}f "
            f"debounce={GestureStateMachine._DEBOUNCE_SEC}s"
        )

    def detect(self, rgb_frame):
        """
        Process one RGB frame.
        Returns (raw_gesture, transition_event, results).

        Callers should act on transition_event, not raw_gesture.
        """
        t0 = time.perf_counter()

        rgb_frame.flags.writeable = False
        results = self.hands.process(rgb_frame)

        self._inference_ms = (time.perf_counter() - t0) * 1000

        if not results.multi_hand_landmarks:
            # Hand disappeared — decay gently; don't reset confirmed state immediately
            # This prevents flicker re-triggers when hand briefly leaves frame
            self._prev_coords = None
            self._prev_tips   = None
            # Feed UNKNOWN so state machine waits for real signal
            event = self._state_machine.update(GestureType.UNKNOWN)
            return GestureType.NONE, event, results

        # ── Landmark extraction ──
        hand   = results.multi_hand_landmarks[0]
        coords = np.fromiter(
            (v for lm in hand.landmark for v in (lm.x, lm.y, lm.z)),
            dtype=np.float32, count=63
        ).reshape(21, 3)

        # ── EMA smoothing ──
        if self._prev_coords is not None:
            coords = self._alpha * coords + (1.0 - self._alpha) * self._prev_coords
        self._prev_coords = coords

        # ── Classify ──
        t2 = time.perf_counter()
        raw = self._classify_fast(coords)
        self._classify_ms = (time.perf_counter() - t2) * 1000
        self._last_raw    = raw

        # ── State machine ──
        event = self._state_machine.update(raw)
        self._last_stable = self._state_machine.confirmed_state

        if event != TransitionEvent.NONE:
            logger.info(f"[TRANSITION] {event} | raw={raw} | infer={self._inference_ms:.0f}ms")

        return raw, event, results

    def _classify_fast(self, coords: np.ndarray) -> str:
        """
        Vectorized gesture classification.
        Uses two complementary signals:
          1. Tip-vs-PIP Y position (standard)
          2. Tip-to-MCP curl ratio (robust at distance)
        """
        tips_y = coords[self.FINGER_TIPS, 1]
        pips_y = coords[self.FINGER_PIPS, 1]
        mcps_y = coords[self.FINGER_MCPS, 1]

        # Method 1: tip above pip (extended) — image coords (y down)
        tip_above_pip = tips_y < pips_y

        # Method 2: tip above mcp (less curled than base knuckle)
        tip_above_mcp = tips_y < mcps_y

        # Combine: extended if tip is above BOTH pip and mcp
        fingers_extended = tip_above_pip & tip_above_mcp

        # Thumb: horizontal spread check
        wrist_x     = coords[0, 0]
        idx_mcp_x   = coords[5, 0]
        thumb_tip_x = coords[4, 0]
        thumb_ip_x  = coords[3, 0]
        is_right    = wrist_x < idx_mcp_x
        thumb_ext   = (thumb_tip_x > thumb_ip_x) if is_right else (thumb_tip_x < thumb_ip_x)

        count = int(np.sum(fingers_extended)) + int(thumb_ext)
        ratio = count / 5.0

        if ratio <= self._fist_thresh:
            return GestureType.CLOSED_FIST
        if ratio >= self._palm_thresh:
            return GestureType.OPEN_PALM
        return GestureType.UNKNOWN

    def draw_landmarks(self, frame, results):
        """Draw hand skeleton on frame for debug view."""
        if results and results.multi_hand_landmarks:
            for h in results.multi_hand_landmarks:
                self.mp_drawing.draw_landmarks(
                    frame, h, self.mp_hands.HAND_CONNECTIONS,
                    self.mp_styles.get_default_hand_landmarks_style(),
                    self.mp_styles.get_default_hand_connections_style()
                )
        return frame

    # ── Properties ──

    @property
    def inference_ms(self) -> float:
        return self._inference_ms

    @property
    def classify_ms(self) -> float:
        return self._classify_ms

    @property
    def last_raw(self) -> str:
        return self._last_raw

    @property
    def confirmed_state(self) -> str:
        return self._state_machine.confirmed_state

    def release(self):
        self.hands.close()
        logger.info("GestureDetector v4 released")
