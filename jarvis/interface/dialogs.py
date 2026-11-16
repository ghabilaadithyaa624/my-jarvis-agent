"""Security Permission Confirmation Modal Dialog with Fail-Closed Countdown."""

from __future__ import annotations

import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from jarvis.security.policy import ConfirmationRequest


class PermissionConfirmDialog(QDialog):
    """Modal confirmation gate shown before any SENSITIVE or CRITICAL action."""

    def __init__(self, req: ConfirmationRequest, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"JARVIS Security Gate — {req.risk_level.value} Action")
        self.setMinimumWidth(520)
        self.setModal(True)
        self._remaining = max(10, int(req.timeout_sec))

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        badge = QLabel(f"CONFIRMATION REQUIRED  •  RISK: {req.risk_level.value}")
        badge.setObjectName("PillDanger")
        layout.addWidget(badge)

        reason_lbl = QLabel(req.reason)
        reason_lbl.setWordWrap(True)
        reason_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #F8FAFC;")
        layout.addWidget(reason_lbl)

        meta = QLabel(f"Tool: {req.tool_name}\nTarget: {req.target_summary}")
        meta.setWordWrap(True)
        meta.setStyleSheet("color: #93C5FD;")
        layout.addWidget(meta)

        args_box = QPlainTextEdit()
        args_box.setReadOnly(True)
        args_box.setMaximumHeight(120)
        args_box.setPlainText(json.dumps(req.arguments, indent=2, ensure_ascii=False, default=str))
        layout.addWidget(args_box)

        self._timer_lbl = QLabel(f"Auto-denying in {self._remaining}s if no response...")
        self._timer_lbl.setObjectName("SubtleLabel")
        layout.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        deny_btn = QPushButton("Deny (Safe Default)")
        deny_btn.clicked.connect(self.reject)
        deny_btn.setDefault(True)
        btn_row.addWidget(deny_btn)

        allow_btn = QPushButton("Approve Once")
        allow_btn.setObjectName("WarnButton")
        allow_btn.clicked.connect(self.accept)
        btn_row.addWidget(allow_btn)

        layout.addLayout(btn_row)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._on_tick)
        self._countdown.start()

    def _on_tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._countdown.stop()
            self.reject()
            return
        self._timer_lbl.setText(f"Auto-denying in {self._remaining}s if no response...")
