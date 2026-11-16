"""Animated JARVIS Arc-Reactor Voice Activity Orb (PySide6 QPainter widget).

Suspends its animation timer automatically while idle so background CPU usage stays ~0%.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget


STATE_PALETTES = {
    "idle": ("#00B4D8", "#0A2540", "STANDBY"),
    "listening": ("#00E5FF", "#083344", "LISTENING"),
    "recording_ptt": ("#38BDF8", "#0C4A6E", "PUSH TO TALK"),
    "wake_triggered": ("#22D3EE", "#164E63", "AWAKE"),
    "transcribing": ("#F59E0B", "#451A03", "TRANSCRIBING"),
    "thinking": ("#FBBF24", "#422006", "PLANNING"),
    "executing": ("#10B981", "#064E3B", "EXECUTING"),
    "speaking": ("#818CF8", "#1E1B4B", "SPEAKING"),
    "muted": ("#64748B", "#1E293B", "MUTED"),
    "unavailable": ("#64748B", "#1E293B", "TEXT MODE"),
    "error": ("#F97316", "#431407", "MIC ALERT"),
    "emergency": ("#EF4444", "#450A0A", "HALTED"),
}


class VoiceOrbWidget(QWidget):
    """Low-overhead custom HUD orb visualizing mic level, speech & execution state."""

    def __init__(self, fps: int = 24, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.setMaximumHeight(210)
        self._state = "idle"
        self._level = 0.0
        self._smoothed_level = 0.0
        self._phase = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(max(16, int(1000 / max(10, fps))))
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str) -> None:
        st = state if state in STATE_PALETTES else "idle"
        if st == self._state:
            return
        self._state = st
        active_states = {
            "listening", "recording_ptt", "wake_triggered",
            "transcribing", "thinking", "executing", "speaking", "emergency",
        }
        if st in active_states:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._level = 0.0
            self._smoothed_level = 0.0
        self.update()

    def set_audio_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))
        if self._level > 0.02 and not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        speed = 3.2 if self._state in {"thinking", "executing", "transcribing"} else 1.4
        self._phase = (self._phase + speed) % 360.0
        target = self._level
        if self._state == "speaking":
            target = max(target, 0.35 + 0.25 * abs(math.sin(math.radians(self._phase * 3))))
        elif self._state in {"thinking", "executing"}:
            target = max(target, 0.25 + 0.15 * math.sin(math.radians(self._phase * 2)))
        self._smoothed_level = 0.65 * self._smoothed_level + 0.35 * target
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        base_r = min(w, h) * 0.34
        pulse = base_r * (0.22 * self._smoothed_level)

        primary_hex, core_hex, caption = STATE_PALETTES.get(self._state, STATE_PALETTES["idle"])
        primary = QColor(primary_hex)
        core_bg = QColor(core_hex)

        # Outer radial halo
        halo = QRadialGradient(QPointF(cx, cy), base_r * 1.45 + pulse)
        glow = QColor(primary)
        glow.setAlpha(int(55 + 95 * self._smoothed_level))
        halo.setColorAt(0.0, glow)
        transparent = QColor(primary)
        transparent.setAlpha(0)
        halo.setColorAt(1.0, transparent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(cx, cy), base_r * 1.4 + pulse, base_r * 1.4 + pulse)

        # Outer segmented rotating ring
        outer_r = base_r * 1.15
        rect_outer = QRectF(cx - outer_r, cy - outer_r, outer_r * 2, outer_r * 2)
        pen_outer = QPen(primary, 2.2)
        painter.setPen(pen_outer)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for seg in range(3):
            start_angle = int((self._phase + seg * 120.0) * 16)
            painter.drawArc(rect_outer, start_angle, 72 * 16)

        # Counter-rotating inner telemetry ring
        mid_r = base_r * 0.94
        rect_mid = QRectF(cx - mid_r, cy - mid_r, mid_r * 2, mid_r * 2)
        dim = QColor(primary)
        dim.setAlpha(150)
        painter.setPen(QPen(dim, 1.5))
        for seg in range(4):
            start_angle = int((-self._phase * 1.3 + seg * 90.0) * 16)
            painter.drawArc(rect_mid, start_angle, 48 * 16)

        # Pulsing arc-reactor core
        core_r = base_r * 0.68 + pulse * 0.65
        core_grad = QRadialGradient(QPointF(cx, cy), core_r)
        center_col = QColor(primary)
        center_col.setAlpha(190)
        core_grad.setColorAt(0.0, core_bg)
        core_grad.setColorAt(0.82, core_bg)
        core_grad.setColorAt(1.0, center_col)
        painter.setPen(QPen(primary, 2.0))
        painter.setBrush(core_grad)
        painter.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # State label inside orb
        font = QFont("Segoe UI", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#F8FAFC"))
        painter.drawText(
            QRectF(cx - core_r, cy - 12, core_r * 2, 24),
            Qt.AlignmentFlag.AlignCenter,
            caption,
        )
        painter.end()
