"""Main PySide6 JARVIS Desktop HUD Window (100% Thread-Safe Signal Architecture)."""

from __future__ import annotations

import sys
import threading
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from jarvis.app.controller import JarvisController
from jarvis.app.events import AppEvent, EventType
from jarvis.interface.camera_widget import CameraWidget
from jarvis.interface.dialogs import PermissionConfirmDialog
from jarvis.interface.panels import (
    ConversationPanel,
    ExecutionMonitorPanel,
    MemoryPanel,
    PermissionsPanel,
    SettingsPanel,
)
from jarvis.interface.theme import JARVIS_DARK_QSS
from jarvis.interface.voice_orb import VoiceOrbWidget
from jarvis.security.policy import ConfirmationRequest
from jarvis.vision.camera_feed import CameraFeed


class _QtEventBridge(QObject):
    """Marshals background worker events and security prompts onto the Qt main thread."""

    app_event = Signal(object)
    confirm_requested = Signal(object, object, object)  # (ConfirmationRequest, list_holder, threading.Event)


class JarvisMainWindow(QMainWindow):
    """Primary JARVIS Personal AI Desktop Operator Window."""

    def __init__(self, controller: JarvisController) -> None:
        super().__init__()
        self.controller = controller
        cfg = controller.config.gui
        self.setWindowTitle(cfg.window_title)
        self.resize(cfg.window_width, cfg.window_height)
        self.setStyleSheet(JARVIS_DARK_QSS)

        self._updating_model_combo = False
        self._camera_active = False
        self._bridge = _QtEventBridge(self)
        self._bridge.app_event.connect(self._on_app_event)
        self._bridge.confirm_requested.connect(self._show_confirmation_dialog)

        self._unsub = self.controller.events.subscribe_all(
            lambda ev: self._bridge.app_event.emit(ev)
        )
        self.controller.security.set_confirmation_handler(self._threadsafe_confirm)

        # Camera components
        cam_idx = getattr(getattr(controller.config, "camera", None), "device_index", 0)
        self._camera_feed = CameraFeed(device_index=cam_idx)
        self._camera_widget = CameraWidget(self._camera_feed)

        self._build_ui()
        self.controller.refresh_ollama_async()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------------- TOP TELEMETRY & MODEL BAR ----------------
        header = QFrame()
        header.setObjectName("HeaderBar")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.setSpacing(8)

        brand = QLabel("JARVIS PERSONAL AI")
        brand.setObjectName("BrandTitle")
        hl.addWidget(brand)

        self.ollama_pill = QLabel("Ollama: Checking...")
        self.ollama_pill.setObjectName("PillOffline")
        hl.addWidget(self.ollama_pill)

        hl.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(190)
        self.model_combo.currentTextChanged.connect(self._on_model_selected)
        hl.addWidget(self.model_combo)

        refresh_mdl_btn = QPushButton("Refresh")
        refresh_mdl_btn.clicked.connect(self.controller.refresh_ollama_async)
        hl.addWidget(refresh_mdl_btn)

        hl.addStretch()

        self.cpu_badge = QLabel("CPU: --%")
        self.cpu_badge.setObjectName("TelemetryBadge")
        self.ram_badge = QLabel("RAM: --%")
        self.ram_badge.setObjectName("TelemetryBadge")
        self.gpu_badge = QLabel("GPU: --")
        self.gpu_badge.setObjectName("TelemetryBadge")
        for b in (self.cpu_badge, self.ram_badge, self.gpu_badge):
            hl.addWidget(b)

        outer.addWidget(header)

        # ---------------- MAIN HUD BODY ----------------
        body = QHBoxLayout()
        body.setContentsMargins(6, 6, 6, 6)
        body.setSpacing(6)

        # 1. Left Voice & Safety Control Dock (compact 160px)
        side = QFrame()
        side.setObjectName("SidePanel")
        side.setFixedWidth(160)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 8, 8, 8)
        sl.setSpacing(6)

        self.orb = VoiceOrbWidget(fps=self.controller.config.gui.orb_fps)
        sl.addWidget(self.orb)

        self.mic_status_lbl = QLabel("Mic: Standby")
        self.mic_status_lbl.setWordWrap(True)
        self.mic_status_lbl.setObjectName("SubtleLabel")
        sl.addWidget(self.mic_status_lbl)

        self.model_info_lbl = QLabel("Local: 127.0.0.1:11434")
        self.model_info_lbl.setWordWrap(True)
        self.model_info_lbl.setObjectName("SubtleLabel")
        sl.addWidget(self.model_info_lbl)

        self.listen_btn = QPushButton("Start Listening")
        self.listen_btn.setObjectName("PrimaryButton")
        self.listen_btn.clicked.connect(self._toggle_listening)
        sl.addWidget(self.listen_btn)

        self.ptt_btn = QPushButton("Hold: PTT")
        self.ptt_btn.setObjectName("PTTButton")
        self.ptt_btn.pressed.connect(self._ptt_down)
        self.ptt_btn.released.connect(self._ptt_up)
        sl.addWidget(self.ptt_btn)

        self.mute_btn = QPushButton(
            "Unmute Voice" if self.controller.speech.is_muted else "Mute Voice"
        )
        self.mute_btn.clicked.connect(self._toggle_mute)
        sl.addWidget(self.mute_btn)

        sl.addStretch()

        self.cancel_btn = QPushButton("Cancel Task")
        self.cancel_btn.setObjectName("WarnButton")
        self.cancel_btn.clicked.connect(self._cancel_task)
        sl.addWidget(self.cancel_btn)

        self.emergency_btn = QPushButton("EMERGENCY STOP")
        self.emergency_btn.setObjectName("EmergencyButton")
        self.emergency_btn.clicked.connect(self._toggle_emergency)
        sl.addWidget(self.emergency_btn)

        body.addWidget(side)

        # 2. Center Tabbed Workspace (stretches to fill space)
        self.tabs = QTabWidget()
        self.conv_panel = ConversationPanel(
            self.controller,
            on_send=lambda txt: self.controller.submit_input(txt, source="gui"),
        )
        self.perm_panel = PermissionsPanel(self.controller)
        self.mem_panel = MemoryPanel(self.controller)
        self.settings_panel = SettingsPanel(self.controller)

        self.tabs.addTab(self.conv_panel, "Operator Console")
        self.tabs.addTab(self.perm_panel, "Permissions && Audit")
        self.tabs.addTab(self.mem_panel, "Memory && Utilities")
        self.tabs.addTab(self.settings_panel, "Settings && Doctor")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        body.addWidget(self.tabs, 1)

        # 3. Right: Compact icon toolbar + Camera panel (hidden by default)
        right_panel = QFrame()
        right_panel.setObjectName("SidePanel")
        right_layout = QHBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        # Vertical icon toolbar
        toolbar = QFrame()
        toolbar.setObjectName("SidePanel")
        toolbar.setFixedWidth(44)
        tb_layout = QVBoxLayout(toolbar)
        tb_layout.setContentsMargins(4, 8, 4, 8)
        tb_layout.setSpacing(6)

        def _icon_btn(emoji: str, tip: str) -> QPushButton:
            btn = QPushButton(emoji)
            btn.setFixedSize(36, 36)
            btn.setToolTip(tip)
            btn.setStyleSheet(
                "QPushButton { font-size:16px; background:#16213e; border:1px solid #0f3460; "
                "border-radius:6px; color:#e2e8f0; } "
                "QPushButton:hover { background:#0f3460; } "
                "QPushButton:pressed { background:#e94560; }"
            )
            return btn

        self._btn_listen = _icon_btn("🎤", "Listen")
        self._btn_listen.clicked.connect(self._toggle_listening)
        tb_layout.addWidget(self._btn_listen)

        self._btn_mute = _icon_btn("🔇", "Mute/Unmute")
        self._btn_mute.clicked.connect(self._toggle_mute)
        tb_layout.addWidget(self._btn_mute)

        self._btn_camera = _icon_btn("📷", "Toggle Camera")
        self._btn_camera.clicked.connect(self._toggle_camera)
        tb_layout.addWidget(self._btn_camera)

        self._btn_speed = _icon_btn("⚡", "Speed: 1.5x")
        self._btn_speed.setStyleSheet(
            self._btn_speed.styleSheet() + " QPushButton { font-size:11px; }"
        )
        self._btn_speed.setText("⚡1.5x")
        self._btn_speed.clicked.connect(self._cycle_speed)
        tb_layout.addWidget(self._btn_speed)

        self._btn_stop = _icon_btn("🛑", "Emergency Stop")
        self._btn_stop.clicked.connect(self._toggle_emergency)
        tb_layout.addWidget(self._btn_stop)

        tb_layout.addStretch()
        right_layout.addWidget(toolbar)

        # Camera widget (hidden by default)
        self._camera_widget.hide()
        right_layout.addWidget(self._camera_widget)

        body.addWidget(right_panel)

        outer.addLayout(body, 1)

        # 4. Execution Monitor below body or as right panel (keep existing)
        self.monitor_panel = ExecutionMonitorPanel()
        body.addWidget(self.monitor_panel)

        # ---------------- STATUS BAR ----------------
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "QStatusBar { background:#0f3460; color:#94a3b8; font-size:11px; padding:2px 8px; }"
        )
        self.setStatusBar(self._status_bar)
        self._status_model_lbl = QLabel("Model: —")
        self._status_voice_lbl = QLabel("Voice: idle")
        self._status_latency_lbl = QLabel("Latency: — ms")
        self._status_bar.addWidget(self._status_model_lbl)
        self._status_bar.addWidget(QLabel(" | "))
        self._status_bar.addWidget(self._status_voice_lbl)
        self._status_bar.addWidget(QLabel(" | "))
        self._status_bar.addWidget(self._status_latency_lbl)

    # ---------------- Thread-Safe Confirmation Gate ----------------

    def _threadsafe_confirm(self, req: ConfirmationRequest) -> bool:
        holder = [False]
        done = threading.Event()
        self._bridge.confirm_requested.emit(req, holder, done)
        done.wait(timeout=req.timeout_sec + 2.0)
        return bool(holder[0])

    def _show_confirmation_dialog(self, req: ConfirmationRequest, holder: list, done: threading.Event) -> None:
        try:
            dlg = PermissionConfirmDialog(req, parent=self)
            holder[0] = dlg.exec() == QDialog.DialogCode.Accepted
        finally:
            done.set()

    # ---------------- UI Button Handlers ----------------

    def _toggle_listening(self) -> None:
        if self.controller.voice.is_running:
            self.controller.stop_listening()
            self.listen_btn.setText("Start Listening")
        else:
            ok, msg = self.controller.start_listening()
            if ok:
                self.listen_btn.setText("Stop Listening")
                if not self.controller.speech.is_muted:
                    self.controller.speech.speak("Voice listening active. I am ready for your instructions.")
            else:
                self.mic_status_lbl.setText(f"Mic: {msg}")
                QMessageBox.information(self, "Voice Input Status", msg)

    def _ptt_down(self) -> None:
        ok, msg = self.controller.voice.ptt_press()
        if not ok:
            self.mic_status_lbl.setText(f"PTT: {msg}")

    def _ptt_up(self) -> None:
        self.controller.voice.ptt_release()

    def _toggle_mute(self) -> None:
        muted = self.controller.toggle_mute()
        self.mute_btn.setText("Unmute Voice" if muted else "Mute Voice")
        if muted:
            self.orb.set_state("muted")
        else:
            self.orb.set_state("listening" if self.controller.voice.is_running else "idle")
            self.controller.speech.speak("Voice output active.")

    def _cancel_task(self) -> None:
        self.controller.cancel_current_task()
        self.conv_panel.end_stream()
        self.orb.set_state("listening" if self.controller.voice.is_running else "idle")

    def _toggle_emergency(self) -> None:
        if self.controller.emergency.is_engaged:
            self.controller.reset_emergency_stop()
        else:
            self.controller.trigger_emergency_stop("GUI Emergency Stop pressed")

    def _toggle_camera(self) -> None:
        if self._camera_active:
            self._camera_widget.stop()
            self._camera_widget.hide()
            self._camera_active = False
        else:
            self._camera_widget.show()
            self._camera_widget.start()
            self._camera_active = True

    def _cycle_speed(self) -> None:
        speeds = [1.0, 1.2, 1.5, 1.8, 2.0]
        curr = getattr(self.controller.config.tts, "speed", 1.5)
        try:
            next_idx = (speeds.index(curr) + 1) % len(speeds)
        except ValueError:
            next_idx = 2
        new_speed = speeds[next_idx]
        self.controller.speech.set_speed(new_speed)
        self.controller.config.tts.speed = new_speed
        self._btn_speed.setText(f"⚡{new_speed}x")
        self._btn_speed.setToolTip(f"Speed: {new_speed}x")

    def _on_model_selected(self, text: str) -> None:
        if self._updating_model_combo or not text:
            return
        model_name = self.model_combo.currentData() or text
        try:
            info = self.controller.switch_model(model_name)
            self.model_info_lbl.setText(
                f"Model: {info['name']} ({info['size_gb']} GB | {info['vram_fit']})"
            )
        except Exception as exc:
            QMessageBox.warning(self, "Model Selection Failed", str(exc))

    def _on_tab_changed(self, idx: int) -> None:
        if idx == 1:
            self.perm_panel.refresh()
        elif idx == 2:
            self.mem_panel.refresh()

    # ---------------- EventBus Subscriber ----------------

    def _on_app_event(self, event: AppEvent) -> None:
        t = event.type
        p = event.payload

        if t == EventType.OLLAMA_STATUS:
            if p.get("online"):
                ver = p.get("version") or "?"
                self.ollama_pill.setText(f"Ollama v{ver} • LOCAL")
                self.ollama_pill.setObjectName("PillOnline")
            else:
                self.ollama_pill.setText("Ollama • OFFLINE")
                self.ollama_pill.setObjectName("PillOffline")
            self.ollama_pill.style().unpolish(self.ollama_pill)
            self.ollama_pill.style().polish(self.ollama_pill)
            info = p.get("active_model_info")
            if info:
                self.model_info_lbl.setText(
                    f"{info['name']} ({info['size_gb']} GB • {info['vram_fit']})"
                )

        elif t == EventType.MODELS_UPDATED:
            self._updating_model_combo = True
            try:
                self.model_combo.clear()
                models = p.get("models") or []
                active = p.get("active_model") or ""
                for m in models:
                    label = f"{m['name']}  ({m['size_gb']} GB)"
                    self.model_combo.addItem(label, m["name"])
                idx = self.model_combo.findData(active)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
                self._status_model_lbl.setText(f"Model: {active or '—'}")
            finally:
                self._updating_model_combo = False

        elif t == EventType.TELEMETRY_UPDATE:
            snap = p.get("snapshot") or {}
            self.cpu_badge.setText(f"CPU: {snap.get('cpu_percent', 0)}%")
            ram_gb = round(snap.get("ram_used_mb", 0) / 1024.0, 1)
            tot_gb = round(snap.get("ram_total_mb", 0) / 1024.0, 0)
            self.ram_badge.setText(f"RAM: {ram_gb}/{int(tot_gb)} GB ({snap.get('ram_percent', 0)}%)")
            gpu = snap.get("gpu") or {}
            if gpu.get("available"):
                self.gpu_badge.setText(
                    f"GPU: {gpu.get('utilization_percent', 0)}% • "
                    f"VRAM {gpu.get('vram_used_mb', 0)}/{gpu.get('vram_total_mb', 0)} MB"
                )
            else:
                self.gpu_badge.setText("GPU: CPU Mode")

        elif t == EventType.VOICE_STATE:
            st = p.get("state", "idle")
            self.orb.set_state(st)
            self.mic_status_lbl.setText(f"Mic: {p.get('detail') or st}")
            self.listen_btn.setText(
                "Stop Listening" if self.controller.voice.is_running else "Start Listening"
            )
            self._status_voice_lbl.setText(f"Voice: {st}")

        elif t == EventType.AUDIO_LEVEL:
            self.orb.set_audio_level(float(p.get("level", 0.0)))

        elif t == EventType.TTS_STATE:
            if p.get("speaking"):
                self.orb.set_state("speaking")
            elif not self.controller.emergency.is_engaged:
                self.orb.set_state("listening" if self.controller.voice.is_running else "idle")

        elif t == EventType.MESSAGE_ADDED:
            self.conv_panel.append_message(
                p.get("role", "assistant"),
                p.get("content", ""),
                verified=p.get("verified"),
                status=p.get("status", ""),
                model=p.get("model", ""),
                source=p.get("source", ""),
            )

        elif t == EventType.STREAM_START:
            self.conv_panel.start_stream()
            self.orb.set_state("thinking")

        elif t == EventType.STREAM_TOKEN:
            self.conv_panel.push_stream_token(p.get("token", ""))

        elif t == EventType.STREAM_END:
            self.conv_panel.end_stream()

        elif t == EventType.TASK_STAGE:
            snum = int(p.get("stage_number", 0))
            sname = str(p.get("stage", ""))
            self.monitor_panel.update_stage(snum, sname, str(p.get("detail", "")))
            if sname in {"intent_interpretation", "task_planning", "tool_selection"}:
                self.orb.set_state("thinking")
            elif sname in {"execution", "verification"}:
                self.orb.set_state("executing")
            elif sname in {"feedback", "cancelled"} and not self.controller.emergency.is_engaged:
                self.orb.set_state("listening" if self.controller.voice.is_running else "idle")

        elif t == EventType.PLAN_CREATED:
            self.monitor_panel.update_plan(p.get("plan") or {})

        elif t == EventType.TOOL_STARTED:
            self.monitor_panel.log_tool_start(
                str(p.get("tool_name", "")), str(p.get("thought", ""))
            )

        elif t == EventType.TOOL_FINISHED:
            dur = float(p.get("duration_ms", 0.0))
            self._status_latency_lbl.setText(f"Latency: {int(dur)} ms")
            self.monitor_panel.log_tool_finish(
                str(p.get("tool_name", "")),
                bool(p.get("success")),
                bool(p.get("verified")),
                bool(p.get("permission_denied")),
                dur,
                str(p.get("summary", "")),
            )

        elif t in {EventType.REMINDER_DUE, EventType.TIMER_FINISHED}:
            self.conv_panel.append_message(
                "assistant", p.get("message", "Alert"), verified=True, status="alert"
            )

        elif t == EventType.EMERGENCY_STOP:
            self.orb.set_state("emergency")
            self.emergency_btn.setText("RESET EMERGENCY STOP")
            self.listen_btn.setText("Start Listening")
            self.monitor_panel.update_stage(0, "EMERGENCY STOP", p.get("reason", ""))

        elif t == EventType.EMERGENCY_RESET:
            self.orb.set_state("idle")
            self.emergency_btn.setText("EMERGENCY STOP")
            self.monitor_panel.update_stage(0, "Ready", "Emergency Stop cleared")

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._unsub()
        except Exception:
            pass
        try:
            if self._camera_active:
                self._camera_widget.stop()
        except Exception:
            pass
        self.controller.shutdown()
        super().closeEvent(event)


def launch_gui(controller: JarvisController) -> int:
    """Launch the PySide6 desktop interface."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("JARVIS PERSONAL AI")
    window = JarvisMainWindow(controller)
    window.show()
    return app.exec()
