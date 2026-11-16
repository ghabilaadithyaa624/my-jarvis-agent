"""Modular Workspace Panels: Conversation, Execution Monitor, Permissions, Memory & Settings."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Callable, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from jarvis.app.controller import JarvisController
from jarvis.app.cli import format_doctor_report


# =====================================================================
# 1. LIVE CONVERSATION & SESSION HISTORY PANEL
# =====================================================================
class ConversationPanel(QWidget):
    def __init__(self, controller: JarvisController, on_send: Callable[[str], None], parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._on_send = on_send
        self._stream_buffer = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Session History drawer
        hist_frame = QFrame()
        hist_frame.setObjectName("Card")
        hist_frame.setMinimumWidth(180)
        hist_frame.setMaximumWidth(240)
        hl = QVBoxLayout(hist_frame)
        hl.setContentsMargins(8, 8, 8, 8)

        lbl = QLabel("CONVERSATION HISTORY")
        lbl.setObjectName("SectionHeader")
        hl.addWidget(lbl)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("+ New")
        new_btn.clicked.connect(self._new_session)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_session)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(del_btn)
        hl.addLayout(btn_row)

        self.session_list = QListWidget()
        self.session_list.setWordWrap(True)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.itemClicked.connect(self._switch_session)
        hl.addWidget(self.session_list, 1)
        splitter.addWidget(hist_frame)

        # Right: Live Transcript + Streaming Box + Input Bar
        chat_widget = QWidget()
        cl = QVBoxLayout(chat_widget)
        cl.setContentsMargins(4, 0, 0, 0)
        cl.setSpacing(8)

        self.chat_view = QTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setPlaceholderText(
            "Say 'Jarvis, open VS Code', 'Jarvis, check my laptop performance', "
            "or type any instruction below..."
        )
        cl.addWidget(self.chat_view, 1)

        self.stream_preview = QLabel("")
        self.stream_preview.setWordWrap(True)
        self.stream_preview.setStyleSheet(
            "background-color: #0D1F38; color: #7DD3FC; border: 1px solid #0284C7; "
            "border-radius: 6px; padding: 6px 10px;"
        )
        self.stream_preview.hide()
        cl.addWidget(self.stream_preview)

        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Give JARVIS a task or ask a question...")
        self.input_box.returnPressed.connect(self._submit)
        input_row.addWidget(self.input_box, 1)

        self.send_btn = QPushButton("Execute")
        self.send_btn.setObjectName("PrimaryButton")
        self.send_btn.clicked.connect(self._submit)
        input_row.addWidget(self.send_btn)
        cl.addLayout(input_row)

        splitter.addWidget(chat_widget)
        splitter.setStretchFactor(1, 4)
        root.addWidget(splitter)
        self.refresh_sessions()

    def _submit(self) -> None:
        text = self.input_box.text().strip()
        if not text:
            return
        self.input_box.clear()
        self._on_send(text)

    def append_message(
        self,
        role: str,
        content: str,
        *,
        verified: Any = None,
        status: str = "",
        model: str = "",
        source: str = "",
    ) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        escaped = html.escape(content).replace("\n", "<br>")
        if role == "user":
            src_tag = " [VOICE]" if source == "voice" else ""
            block = (
                f"<div style='margin:4px 0; padding:6px 10px; background:#0F1E36; "
                f"border-left:3px solid #38BDF8; border-radius:6px;'>"
                f"<span style='color:#38BDF8; font-weight:700; font-size:11px;'>YOU{src_tag}</span> "
                f"<span style='color:#64748B; font-size:10px;'>{ts}</span><br>"
                f"<span style='color:#F1F5F9; font-size:11px;'>{escaped}</span></div>"
            )
        else:
            badge = ""
            if verified is True:
                badge = " <span style='color:#34D399; font-weight:700; font-size:11px;'>[VERIFIED]</span>"
            elif verified is False:
                lbl = (status or "UNVERIFIED").upper()
                badge = f" <span style='color:#FBBF24; font-weight:700; font-size:11px;'>[{html.escape(lbl)}]</span>"
            mdl = f" <span style='color:#64748B; font-size:10px;'>({html.escape(model)})</span>" if model else ""
            block = (
                f"<div style='margin:4px 0; padding:6px 10px; background:#0A1526; "
                f"border-left:3px solid #00D4FF; border-radius:6px;'>"
                f"<span style='color:#00D4FF; font-weight:700; font-size:11px;'>JARVIS</span>{badge}{mdl} "
                f"<span style='color:#64748B; font-size:10px;'>{ts}</span><br>"
                f"<span style='color:#E2E8F0; font-size:11px;'>{escaped}</span></div>"
            )
        self.chat_view.append(block)
        sb = self.chat_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.refresh_sessions()

    def start_stream(self) -> None:
        self._stream_buffer = ""
        self.stream_preview.setText("JARVIS is generating...")
        self.stream_preview.show()

    def push_stream_token(self, token: str) -> None:
        self._stream_buffer += token
        tail = self._stream_buffer[-450:]
        self.stream_preview.setText(tail)

    def end_stream(self) -> None:
        self._stream_buffer = ""
        self.stream_preview.hide()

    def refresh_sessions(self) -> None:
        sessions = self.controller.conversations.list_sessions(limit=40)
        cur = self.controller.conversations.current_session_id
        self.session_list.clear()
        for s in sessions:
            item = QListWidgetItem(s["title"])
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            if s["id"] == cur:
                item.setSelected(True)
            self.session_list.addItem(item)

    def _new_session(self) -> None:
        self.controller.conversations.create_session("New Session")
        self.chat_view.clear()
        self.refresh_sessions()

    def _delete_session(self) -> None:
        item = self.session_list.currentItem()
        if not item:
            return
        sid = item.data(Qt.ItemDataRole.UserRole)
        self.controller.conversations.delete_session(sid)
        self._reload_current_messages()
        self.refresh_sessions()

    def _switch_session(self, item: QListWidgetItem) -> None:
        sid = item.data(Qt.ItemDataRole.UserRole)
        if self.controller.conversations.switch_session(sid):
            self._reload_current_messages()

    def _reload_current_messages(self) -> None:
        self.chat_view.clear()
        for m in self.controller.conversations.get_messages(limit=80):
            self.append_message(
                m["role"],
                m["content"],
                verified=m.get("verified"),
                model=m.get("model", ""),
                status=m.get("metadata", {}).get("status", ""),
            )


# =====================================================================
# 2. RIGHT DOCK: TASK EXECUTION STATUS & TOOL ACTIVITY MONITOR
# =====================================================================
class ExecutionMonitorPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MonitorPanel")
        self.setMinimumWidth(310)
        self.setMaximumWidth(390)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        hdr = QLabel("TASK EXECUTION ENGINE")
        hdr.setObjectName("SectionHeader")
        layout.addWidget(hdr)

        self.stage_label = QLabel("Stage: Idle — Ready for instructions")
        self.stage_label.setWordWrap(True)
        self.stage_label.setStyleSheet(
            "background:#101E36; color:#38BDF8; padding:6px 8px; border-radius:6px; font-weight:600;"
        )
        layout.addWidget(self.stage_label)

        plan_hdr = QLabel("ACTIVE PLAN STEPS")
        plan_hdr.setObjectName("SubtleLabel")
        layout.addWidget(plan_hdr)

        self.plan_list = QListWidget()
        self.plan_list.setWordWrap(True)
        self.plan_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plan_list.setMaximumHeight(165)
        layout.addWidget(self.plan_list)

        tool_hdr = QLabel("LIVE TOOL ACTIVITY MONITOR")
        tool_hdr.setObjectName("SectionHeader")
        layout.addWidget(tool_hdr)

        self.tool_table = QTableWidget(0, 4)
        self.tool_table.setHorizontalHeaderLabels(["Time", "Tool", "Status", "ms"])
        self.tool_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tool_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tool_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tool_table.verticalHeader().setVisible(False)
        self.tool_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.tool_table, 1)

    def update_stage(self, stage_num: int, stage: str, detail: str) -> None:
        prefix = f"[{stage_num}/9] " if stage_num else ""
        label = stage.replace("_", " ").title()
        self.stage_label.setText(f"{prefix}{label}: {detail}" if detail else f"{prefix}{label}")

    def update_plan(self, plan: Dict[str, Any]) -> None:
        self.plan_list.clear()
        icons = {
            "pending": "[ ]",
            "running": "[>]",
            "verified": "[OK]",
            "failed": "[X]",
            "denied": "[!]",
        }
        for step in plan.get("steps", []):
            st = step.get("status", "pending")
            mark = icons.get(st, "[ ]")
            self.plan_list.addItem(f"{mark} {step.get('index')}. {step.get('description')}")

    def log_tool_start(self, tool_name: str, thought: str) -> None:
        row = 0
        self.tool_table.insertRow(row)
        self.tool_table.setItem(row, 0, QTableWidgetItem(datetime.now().strftime("%H:%M:%S")))
        t_item = QTableWidgetItem(tool_name)
        t_item.setToolTip(thought)
        self.tool_table.setItem(row, 1, t_item)
        self.tool_table.setItem(row, 2, QTableWidgetItem("RUNNING..."))
        self.tool_table.setItem(row, 3, QTableWidgetItem("-"))
        if self.tool_table.rowCount() > 80:
            self.tool_table.removeRow(self.tool_table.rowCount() - 1)

    def log_tool_finish(
        self,
        tool_name: str,
        success: bool,
        verified: bool,
        denied: bool,
        duration_ms: float,
        summary: str,
    ) -> None:
        status = "DENIED" if denied else ("VERIFIED" if (success and verified) else ("PARTIAL" if success else "FAILED"))
        # Update matching top RUNNING row if present
        for r in range(min(5, self.tool_table.rowCount())):
            t_item = self.tool_table.item(r, 1)
            s_item = self.tool_table.item(r, 2)
            if t_item and s_item and t_item.text() == tool_name and s_item.text() == "RUNNING...":
                s_item.setText(status)
                s_item.setToolTip(summary)
                self.tool_table.setItem(r, 3, QTableWidgetItem(f"{int(duration_ms)}"))
                return
        row = 0
        self.tool_table.insertRow(row)
        self.tool_table.setItem(row, 0, QTableWidgetItem(datetime.now().strftime("%H:%M:%S")))
        self.tool_table.setItem(row, 1, QTableWidgetItem(tool_name))
        s_item = QTableWidgetItem(status)
        s_item.setToolTip(summary)
        self.tool_table.setItem(row, 2, s_item)
        self.tool_table.setItem(row, 3, QTableWidgetItem(f"{int(duration_ms)}"))


# =====================================================================
# 3. SECURITY PERMISSIONS & AUDIT LOG PANEL
# =====================================================================
class PermissionsPanel(QWidget):
    def __init__(self, controller: JarvisController, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        top_row = QHBoxLayout()

        # Permitted Directories card
        dir_card = QFrame()
        dir_card.setObjectName("Card")
        dl = QVBoxLayout(dir_card)
        dl.addWidget(QLabel("PERMITTED DIRECTORIES"))
        self.dir_list = QListWidget()
        dl.addWidget(self.dir_list, 1)
        d_btns = QHBoxLayout()
        add_dir_btn = QPushButton("Add Folder...")
        add_dir_btn.clicked.connect(self._add_dir)
        rem_dir_btn = QPushButton("Remove Selected")
        rem_dir_btn.clicked.connect(self._remove_dir)
        d_btns.addWidget(add_dir_btn)
        d_btns.addWidget(rem_dir_btn)
        dl.addLayout(d_btns)
        top_row.addWidget(dir_card, 2)

        # Allowlisted Applications card
        app_card = QFrame()
        app_card.setObjectName("Card")
        al = QVBoxLayout(app_card)
        al.addWidget(QLabel("APPLICATION ALLOWLIST"))
        self.app_list = QListWidget()
        al.addWidget(self.app_list, 1)
        a_btns = QHBoxLayout()
        self.app_input = QLineEdit()
        self.app_input.setPlaceholderText("App name (e.g. gimp)")
        add_app_btn = QPushButton("Add")
        add_app_btn.clicked.connect(self._add_app)
        rem_app_btn = QPushButton("Remove")
        rem_app_btn.clicked.connect(self._remove_app)
        a_btns.addWidget(self.app_input, 1)
        a_btns.addWidget(add_app_btn)
        a_btns.addWidget(rem_app_btn)
        al.addLayout(a_btns)
        top_row.addWidget(app_card, 2)

        # Confirmation Safeguards card
        pol_card = QFrame()
        pol_card.setObjectName("Card")
        pl = QVBoxLayout(pol_card)
        pl.addWidget(QLabel("CONFIRMATION SAFEGUARDS"))
        sec = controller.config.security
        self.chk_del = QCheckBox("Confirm file deletions")
        self.chk_del.setChecked(sec.require_confirmation_for_delete)
        self.chk_ovr = QCheckBox("Confirm file overwrites")
        self.chk_ovr.setChecked(sec.require_confirmation_for_overwrite)
        self.chk_ins = QCheckBox("Confirm software installs")
        self.chk_ins.setChecked(sec.require_confirmation_for_install)
        self.chk_adm = QCheckBox("Confirm admin escalation")
        self.chk_adm.setChecked(sec.require_confirmation_for_admin)
        self.chk_prc = QCheckBox("Confirm stopping processes")
        self.chk_prc.setChecked(sec.require_confirmation_for_process_stop)
        self.chk_web = QCheckBox("Confirm sensitive/payment web actions")
        self.chk_web.setChecked(sec.require_confirmation_for_sensitive_web)
        for chk in (self.chk_del, self.chk_ovr, self.chk_ins, self.chk_adm, self.chk_prc, self.chk_web):
            chk.toggled.connect(self._save_policy)
            pl.addWidget(chk)
        pl.addStretch()
        top_row.addWidget(pol_card, 2)
        layout.addLayout(top_row, 1)

        # Audit Log table + hash-chain integrity verifier
        audit_hdr = QHBoxLayout()
        lbl = QLabel("TAMPER-EVIDENT SECURITY AUDIT LOG (SHA-256 CHAINED)")
        lbl.setObjectName("SectionHeader")
        audit_hdr.addWidget(lbl)
        audit_hdr.addStretch()
        verify_btn = QPushButton("Verify Hash Chain")
        verify_btn.clicked.connect(self._verify_audit)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        audit_hdr.addWidget(verify_btn)
        audit_hdr.addWidget(refresh_btn)
        layout.addLayout(audit_hdr)

        self.audit_table = QTableWidget(0, 5)
        self.audit_table.setHorizontalHeaderLabels(["Timestamp", "Event", "Action", "Decision", "Risk"])
        self.audit_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.audit_table.verticalHeader().setVisible(False)
        self.audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.audit_table, 1)
        self.refresh()

    def refresh(self) -> None:
        self.dir_list.clear()
        for d in self.controller.config.security.permitted_directories:
            self.dir_list.addItem(d)
        self.app_list.clear()
        for a in sorted(self.controller.config.security.allowlisted_apps):
            self.app_list.addItem(a)
        entries = self.controller.audit.read_recent(limit=60)
        self.audit_table.setRowCount(len(entries))
        for idx, e in enumerate(entries):
            self.audit_table.setItem(idx, 0, QTableWidgetItem(str(e.get("timestamp", ""))[:19]))
            self.audit_table.setItem(idx, 1, QTableWidgetItem(str(e.get("event_type", ""))))
            self.audit_table.setItem(idx, 2, QTableWidgetItem(str(e.get("action", ""))))
            self.audit_table.setItem(idx, 3, QTableWidgetItem(str(e.get("decision", ""))))
            self.audit_table.setItem(idx, 4, QTableWidgetItem(str(e.get("risk_level", ""))))

    def _add_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Permitted Directory")
        if not folder:
            return
        try:
            self.controller.security.add_permitted_directory(folder)
            self.controller.save_settings()
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Permission Rejected", str(exc))

    def _remove_dir(self) -> None:
        item = self.dir_list.currentItem()
        if item:
            self.controller.security.remove_permitted_directory(item.text())
            self.controller.save_settings()
            self.refresh()

    def _add_app(self) -> None:
        name = self.app_input.text().strip()
        if name:
            self.controller.security.add_allowlisted_app(name)
            self.app_input.clear()
            self.controller.save_settings()
            self.refresh()

    def _remove_app(self) -> None:
        item = self.app_list.currentItem()
        if item:
            self.controller.security.remove_allowlisted_app(item.text())
            self.controller.save_settings()
            self.refresh()

    def _save_policy(self) -> None:
        sec = self.controller.config.security
        sec.require_confirmation_for_delete = self.chk_del.isChecked()
        sec.require_confirmation_for_overwrite = self.chk_ovr.isChecked()
        sec.require_confirmation_for_install = self.chk_ins.isChecked()
        sec.require_confirmation_for_admin = self.chk_adm.isChecked()
        sec.require_confirmation_for_process_stop = self.chk_prc.isChecked()
        sec.require_confirmation_for_sensitive_web = self.chk_web.isChecked()
        self.controller.save_settings()

    def _verify_audit(self) -> None:
        ok, _cnt, msg = self.controller.audit.verify_chain()
        if ok:
            QMessageBox.information(self, "Audit Chain Verified", msg)
        else:
            QMessageBox.critical(self, "Audit Tamper Warning", msg)


# =====================================================================
# 4. PERSISTENT MEMORY & PERSONAL UTILITIES PANEL
# =====================================================================
class MemoryPanel(QWidget):
    def __init__(self, controller: JarvisController, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)

        sub_tabs = QTabWidget()

        # Subtab A: Long-term Facts
        facts_w = QWidget()
        fl = QVBoxLayout(facts_w)
        add_row = QHBoxLayout()
        self.fact_key = QLineEdit()
        self.fact_key.setPlaceholderText("Key (e.g. blender_edu_project)")
        self.fact_val = QLineEdit()
        self.fact_val.setPlaceholderText("Value (e.g. D:\\Projects\\SolarSystem.blend)")
        self.fact_pin = QCheckBox("Pin")
        save_fact_btn = QPushButton("Save Fact")
        save_fact_btn.setObjectName("PrimaryButton")
        save_fact_btn.clicked.connect(self._add_fact)
        del_fact_btn = QPushButton("Delete Selected")
        del_fact_btn.clicked.connect(self._delete_fact)
        for w in (self.fact_key, self.fact_val, self.fact_pin, save_fact_btn, del_fact_btn):
            add_row.addWidget(w)
        fl.addLayout(add_row)

        self.facts_table = QTableWidget(0, 4)
        self.facts_table.setHorizontalHeaderLabels(["ID", "Key", "Value", "Pinned"])
        self.facts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.facts_table.verticalHeader().setVisible(False)
        self.facts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        fl.addWidget(self.facts_table, 1)
        sub_tabs.addTab(facts_w, "Persistent Memory")

        # Subtab B: To-Dos & Reminders
        todo_w = QWidget()
        tl = QVBoxLayout(todo_w)
        t_row = QHBoxLayout()
        self.todo_input = QLineEdit()
        self.todo_input.setPlaceholderText("New to-do item...")
        add_todo_btn = QPushButton("Add To-Do")
        add_todo_btn.clicked.connect(self._add_todo)
        done_todo_btn = QPushButton("Mark Completed")
        done_todo_btn.clicked.connect(self._complete_todo)
        t_row.addWidget(self.todo_input, 1)
        t_row.addWidget(add_todo_btn)
        t_row.addWidget(done_todo_btn)
        tl.addLayout(t_row)
        self.todo_list = QListWidget()
        tl.addWidget(self.todo_list, 1)
        sub_tabs.addTab(todo_w, "To-Do List")

        # Subtab C: Task History
        hist_w = QWidget()
        hl = QVBoxLayout(hist_w)
        ref_btn = QPushButton("Refresh All")
        ref_btn.clicked.connect(self.refresh)
        hl.addWidget(ref_btn)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["Time", "Instruction", "Status", "Verified", "ms"])
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hl.addWidget(self.history_table, 1)
        sub_tabs.addTab(hist_w, "Task History")

        layout.addWidget(sub_tabs)
        self.refresh()

    def refresh(self) -> None:
        facts = self.controller.knowledge.list_all(limit=100)
        self.facts_table.setRowCount(len(facts))
        for i, f in enumerate(facts):
            self.facts_table.setItem(i, 0, QTableWidgetItem(str(f["id"])))
            self.facts_table.setItem(i, 1, QTableWidgetItem(f["key"]))
            self.facts_table.setItem(i, 2, QTableWidgetItem(f["value"]))
            self.facts_table.setItem(i, 3, QTableWidgetItem("Yes" if f["pinned"] else "No"))

        self.todo_list.clear()
        for td in self.controller.utilities.list_todos("all", limit=60):
            mark = "[x]" if td["status"] == "completed" else "[ ]"
            item = QListWidgetItem(f"{mark} #{td['id']} [{td['priority']}] {td['title']}")
            item.setData(Qt.ItemDataRole.UserRole, td["id"])
            self.todo_list.addItem(item)

        hist = self.controller.utilities.get_task_history(limit=50)
        self.history_table.setRowCount(len(hist))
        for i, h in enumerate(hist):
            self.history_table.setItem(i, 0, QTableWidgetItem(str(h["created_at"])[:19]))
            self.history_table.setItem(i, 1, QTableWidgetItem(h["user_input"]))
            self.history_table.setItem(i, 2, QTableWidgetItem(h["status"]))
            self.history_table.setItem(i, 3, QTableWidgetItem("Yes" if h["verified"] else "No"))
            self.history_table.setItem(i, 4, QTableWidgetItem(str(int(h["duration_ms"]))))

    def _add_fact(self) -> None:
        k, v = self.fact_key.text().strip(), self.fact_val.text().strip()
        if not k or not v:
            return
        self.controller.knowledge.remember(k, v, pinned=self.fact_pin.isChecked())
        self.fact_key.clear()
        self.fact_val.clear()
        self.refresh()

    def _delete_fact(self) -> None:
        row = self.facts_table.currentRow()
        if row >= 0:
            id_item = self.facts_table.item(row, 0)
            if id_item:
                self.controller.knowledge.delete(int(id_item.text()))
                self.refresh()

    def _add_todo(self) -> None:
        t = self.todo_input.text().strip()
        if t:
            self.controller.utilities.add_todo(t)
            self.todo_input.clear()
            self.refresh()

    def _complete_todo(self) -> None:
        item = self.todo_list.currentItem()
        if item:
            tid = item.data(Qt.ItemDataRole.UserRole)
            self.controller.utilities.update_todo(int(tid), "completed")
            self.refresh()


# =====================================================================
# 5. SETTINGS & ENVIRONMENT DIAGNOSTICS PANEL
# =====================================================================
class SettingsPanel(QWidget):
    def __init__(self, controller: JarvisController, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        cfg = controller.config

        layout = QHBoxLayout(self)
        layout.setSpacing(12)

        form_card = QFrame()
        form_card.setObjectName("Card")
        form = QFormLayout(form_card)
        form.setSpacing(10)

        self.lang_combo = QComboBox()
        for code, prof in cfg.languages.items():
            self.lang_combo.addItem(f"{prof.display_name} ({code})", code)
        idx = self.lang_combo.findData(cfg.agent.active_language)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        form.addRow("Conversation Language:", self.lang_combo)

        self.mode_combo = QComboBox()
        for mode in ("wake_word", "continuous", "push_to_talk"):
            self.mode_combo.addItem(mode, mode)
        self.mode_combo.setCurrentText(cfg.stt.listen_mode)
        form.addRow("Voice Listen Mode:", self.mode_combo)

        self.stt_size_combo = QComboBox()
        for sz in ("tiny", "base", "small", "medium"):
            self.stt_size_combo.addItem(sz, sz)
        self.stt_size_combo.setCurrentText(cfg.stt.model_size)
        form.addRow("Whisper Model (CPU int8):", self.stt_size_combo)

        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(50, 200)
        self.speed_slider.setValue(int(cfg.tts.speed * 100))
        form.addRow("Speaking Speed (50-200%):", self.speed_slider)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(int(cfg.tts.volume * 100))
        form.addRow("Speech Volume (0-100%):", self.vol_slider)

        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(1024, 16384)
        self.ctx_spin.setSingleStep(1024)
        self.ctx_spin.setValue(cfg.ollama.num_ctx)
        form.addRow("Ollama Context (num_ctx):", self.ctx_spin)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.5)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setValue(cfg.ollama.temperature)
        form.addRow("LLM Temperature:", self.temp_spin)

        self.barge_chk = QCheckBox("Enable Voice Interrupt (Barge-In)")
        self.barge_chk.setChecked(cfg.stt.barge_in_enabled)
        form.addRow("", self.barge_chk)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self._apply)
        test_tts_btn = QPushButton("Test Voice")
        test_tts_btn.clicked.connect(
            lambda: self.controller.speech.speak("Jarvis local voice engine is online and ready.")
        )
        unload_btn = QPushButton("Free GPU VRAM")
        unload_btn.clicked.connect(self._free_vram)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(test_tts_btn)
        btn_row.addWidget(unload_btn)
        form.addRow("", btn_row)

        layout.addWidget(form_card, 2)

        # Right: Live Environment Doctor Output
        doc_card = QFrame()
        doc_card.setObjectName("Card")
        dl = QVBoxLayout(doc_card)
        dh = QHBoxLayout()
        lbl = QLabel("ENVIRONMENT & HARDWARE DOCTOR")
        lbl.setObjectName("SectionHeader")
        dh.addWidget(lbl)
        dh.addStretch()
        run_doc_btn = QPushButton("Run Inspection")
        run_doc_btn.clicked.connect(self._run_doctor)
        dh.addWidget(run_doc_btn)
        dl.addLayout(dh)

        self.doctor_box = QPlainTextEdit()
        self.doctor_box.setReadOnly(True)
        self.doctor_box.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        self.doctor_box.setPlaceholderText("Click 'Run Inspection' to audit Ollama, RTX 3050 VRAM, audio & dependencies.")
        dl.addWidget(self.doctor_box, 1)
        layout.addWidget(doc_card, 3)

    def _apply(self) -> None:
        cfg = self.controller.config
        lang_code = self.lang_combo.currentData()
        self.controller.set_language(lang_code)
        self.controller.voice.set_mode(self.mode_combo.currentData())
        cfg.stt.model_size = self.stt_size_combo.currentData()
        cfg.stt.barge_in_enabled = self.barge_chk.isChecked()
        self.controller.speech.set_speed(self.speed_slider.value() / 100.0)
        self.controller.speech.set_volume(self.vol_slider.value() / 100.0)
        cfg.ollama.num_ctx = self.ctx_spin.value()
        cfg.ollama.temperature = round(self.temp_spin.value(), 2)
        path = self.controller.save_settings()
        QMessageBox.information(self, "Settings Saved", f"Configuration saved locally to:\n{path}")

    def _free_vram(self) -> None:
        ok = self.controller.models.unload_active_model()
        msg = "Unloaded active Ollama model from GPU VRAM." if ok else "No loaded Ollama model to evict."
        QMessageBox.information(self, "GPU Memory", msg)

    def _run_doctor(self) -> None:
        report = self.controller.run_environment_doctor()
        self.doctor_box.setPlainText(format_doctor_report(report))
