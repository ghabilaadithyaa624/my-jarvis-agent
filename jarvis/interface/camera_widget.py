"""Live camera feed widget for JARVIS HUD."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class CameraWidget(QWidget):
    def __init__(self, camera_feed, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._feed = camera_feed
        self._label = QLabel("Camera Off")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "background-color: #070B14; color: #64748B; border: 1px solid #1E293B; "
            "border-radius: 8px; font-size: 12px; font-weight: 600;"
        )
        self._label.setFixedSize(280, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_frame)
        self._frame_count = 0

    def start(self) -> None:
        ok = self._feed.start()
        self._frame_count = 0
        if ok:
            self._label.setText("Connecting camera...")
            self._timer.start(67)  # ~15 FPS
        else:
            self._label.setText("Camera Unavailable")

    def stop(self) -> None:
        self._timer.stop()
        self._feed.stop()
        self._label.setText("Camera Off")

    def _update_frame(self) -> None:
        img = self._feed.get_qimage()
        if img:
            self._frame_count += 1
            self._label.setPixmap(
                QPixmap.fromImage(img).scaled(
                    self._label.width(),
                    self._label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        elif self._frame_count == 0:
            self._label.setText("Waiting for feed...")
