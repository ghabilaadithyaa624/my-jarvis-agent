"""Thread-safe webcam capture for JARVIS with DirectShow and standard fallback."""
from __future__ import annotations

import threading
from typing import Optional

import cv2
from PySide6.QtGui import QImage


class CameraFeed:
    def __init__(self, device_index: int = 0) -> None:
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._running = False
        self._device_index = device_index

    def start(self) -> bool:
        with self._lock:
            if self._running and self._cap and self._cap.isOpened():
                return True
            try:
                self._cap = cv2.VideoCapture(self._device_index, cv2.CAP_DSHOW)
                if not self._cap or not self._cap.isOpened():
                    self._cap = cv2.VideoCapture(self._device_index)
            except Exception:
                self._cap = cv2.VideoCapture(self._device_index)

            if self._cap and self._cap.isOpened():
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                self._cap.set(cv2.CAP_PROP_FPS, 15)
                self._running = True
                return True
            self._running = False
            return False

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

    def get_qimage(self) -> Optional[QImage]:
        if not self._running:
            return None
        with self._lock:
            if not self._cap or not self._cap.isOpened():
                return None
            ret, frame = self._cap.read()
        if not ret or frame is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        return QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
