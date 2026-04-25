from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from queue import Empty, Queue
from threading import Thread
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path

import PySide6
from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from desktop_app_core import *
from desktop_app_workers import *
from desktop_app_ui import *

class WindowSessionMixin:
    def is_session_running(self, session_id: str | None) -> bool:
                if not session_id:
                    return False
                worker = self.workers.get(session_id)
                return bool(worker and worker.isRunning())

    def mark_session_unread(self, session_id: str | None) -> None:
                if not session_id or session_id == self.active_session_id:
                    return
                if session_id in self.session_unread_ids:
                    return
                self.session_unread_ids.add(session_id)
                self.refresh_session_list()

    def clear_session_unread(self, session_id: str | None) -> None:
                if not session_id or session_id not in self.session_unread_ids:
                    return
                self.session_unread_ids.discard(session_id)
                self.refresh_session_list()

    def on_search(self, text: str) -> None:
                self.apply_session_filters()

    def set_session_scope(self, scope: str) -> None:
                if scope not in {"all", "pinned", "recent"}:
                    return
                self.session_scope = scope
                self.apply_session_filters()

    def apply_session_filters(self) -> None:
                self.update_session_scope_buttons()
                sessions = self.sessions[:]
                if self.session_scope == "pinned":
                    sessions = [s for s in sessions if s.session_id in self.pinned_session_ids]
                elif self.session_scope == "recent":
                    cutoff = datetime.now().astimezone() - timedelta(days=7)
                    sessions = [s for s in sessions if session_sort_key(s.updated_at_raw) >= cutoff]

                query = self.search.text().strip().lower()
                if query:
                    sessions = [
                        s for s in sessions if query in s.thread_name.lower() or query in s.session_id.lower()
                    ]
                self.filtered_sessions = sessions
                self.visible_session_limit = self.session_page_size
                self.refresh_session_list()

    def update_session_scope_buttons(self) -> None:
                scope_buttons = {
                    "all": self.scope_all_button,
                    "pinned": self.scope_pinned_button,
                    "recent": self.scope_recent_button,
                }
                for scope, button in scope_buttons.items():
                    selected = scope == self.session_scope
                    button.setProperty("selected", selected)
                    button.style().unpolish(button)
                    button.style().polish(button)

    def refresh_session_list(self) -> None:
                visible_sessions = self.filtered_sessions[: self.visible_session_limit]
                query = self.search.text().strip()
                self.session_list.blockSignals(True)
                self.session_list.clear()
                current_row = -1

                pinned_sessions = [s for s in visible_sessions if s.session_id in self.pinned_session_ids]
                regular_sessions = [s for s in visible_sessions if s.session_id not in self.pinned_session_ids]

                def add_group(title: str, group_sessions: list[SessionSummary], time_grouped: bool) -> None:
                    nonlocal current_row
                    previous_group = ""
                    if not group_sessions:
                        return
                    if not time_grouped:
                        header_item = QListWidgetItem()
                        header_item.setFlags(Qt.NoItemFlags)
                        header_widget = SessionGroupHeader(title)
                        header_item.setSizeHint(header_widget.sizeHint())
                        self.session_list.addItem(header_item)
                        self.session_list.setItemWidget(header_item, header_widget)
                    for session in group_sessions:
                        if time_grouped:
                            group = session_group_label(session.updated_at_raw)
                            if group != previous_group:
                                header_item = QListWidgetItem()
                                header_item.setFlags(Qt.NoItemFlags)
                                header_widget = SessionGroupHeader(group)
                                header_item.setSizeHint(header_widget.sizeHint())
                                self.session_list.addItem(header_item)
                                self.session_list.setItemWidget(header_item, header_widget)
                                previous_group = group

                        item = QListWidgetItem()
                        item.setData(Qt.UserRole, session.session_id)
                        preview_widget = SessionListItem(
                            session,
                            False,
                            query,
                            running=self.is_session_running(session.session_id),
                            unread=session.session_id in self.session_unread_ids,
                        )
                        item.setSizeHint(preview_widget.sizeHint())
                        self.session_list.addItem(item)
                        selected = session.session_id == self.active_session_id
                        self.session_list.setItemWidget(
                            item,
                            SessionListItem(
                                session,
                                selected,
                                query,
                                running=self.is_session_running(session.session_id),
                                unread=session.session_id in self.session_unread_ids,
                            ),
                        )
                        if selected:
                            current_row = self.session_list.row(item)

                add_group("置顶", pinned_sessions, time_grouped=False)
                add_group("", regular_sessions, time_grouped=True)

                if current_row >= 0:
                    self.session_list.setCurrentRow(current_row)
                elif visible_sessions:
                    for i in range(self.session_list.count()):
                        item = self.session_list.item(i)
                        if item and item.data(Qt.UserRole):
                            self.session_list.setCurrentRow(i)
                            break
                elif self.active_session_id is not None:
                    self.active_session_id = None
                self.session_list.blockSignals(False)
                self.load_more_button.setVisible(len(self.filtered_sessions) > len(visible_sessions))

    def refresh_sessions_for_account(self, keep_selection: bool = True) -> None:
                previous = self.active_session_id if keep_selection else None
                self.sessions = load_sessions(self.config, session_aliases=self.session_aliases)
                visible_ids = {session.session_id for session in self.sessions}
                self.active_session_id = previous if previous in visible_ids else (self.sessions[0].session_id if self.sessions else None)
                self.apply_session_filters()
                self.update_work_dir_label()
                self.load_active_session(scroll_to_top=False)

    def load_more_sessions(self) -> None:
                self.visible_session_limit += self.session_page_size
                self.refresh_session_list()

    def update_pin_button(self) -> None:
                pinned = bool(self.active_session_id and self.active_session_id in self.pinned_session_ids)
                self.pin_button.setText("取消置顶" if pinned else "置顶")
                self.pin_button.setEnabled(bool(self.active_session_id))

    def update_session_action_buttons(self) -> None:
                has_session = bool(self.active_session_id)
                self.copy_resume_button.setEnabled(has_session)
                self.rename_session_button.setEnabled(has_session)
                self.copy_session_id_button.setEnabled(has_session)
                self.open_session_file_button.setEnabled(has_session and bool(find_session_file(self.config.codex_home, self.active_session_id)))
                self.clear_session_alias_button.setEnabled(has_session and self.active_session_id in self.session_aliases)
                self.permission_combo.setEnabled(True)

    def permission_preset_for_config(self) -> str:
                return permission_preset_from_runtime(self.config.approval_policy, self.config.sandbox_mode)

    def update_permission_selector(self) -> None:
                preset = self.permission_preset_for_config()
                index = self.permission_combo.findData(preset)
                self.permission_combo.blockSignals(True)
                if index >= 0:
                    self.permission_combo.setCurrentIndex(index)
                self.permission_combo.blockSignals(False)

    def on_permission_preset_changed(self) -> None:
                preset = self.permission_combo.currentData()
                approval_policy, sandbox_mode = runtime_from_permission_preset(preset)
                if (
                    approval_policy == self.config.approval_policy
                    and sandbox_mode == self.config.sandbox_mode
                ):
                    return
                self.config.approval_policy = approval_policy
                self.config.sandbox_mode = sandbox_mode
                self.config.full_auto = approval_policy == "on-request" and sandbox_mode == "workspace-write"
                save_config(self.config)
                self.update_permission_selector()

    def refresh_sessions_after_alias_update(self) -> None:
                current = self.active_session_id
                self.sessions = load_sessions(self.config, session_aliases=self.session_aliases)
                if current:
                    self.active_session_id = current
                self.apply_session_filters()
                self.load_active_session(scroll_to_top=False)

    def rename_current_session(self) -> None:
                if not self.active_session_id:
                    return
                current_title = next(
                    (session.thread_name for session in self.sessions if session.session_id == self.active_session_id),
                    self.active_session_id[:8],
                )
                new_title, accepted = QInputDialog.getText(self, "重命名会话", "本地别名", text=current_title)
                if not accepted:
                    return
                alias = new_title.strip()
                if not alias:
                    self.clear_current_session_alias()
                    return
                self.session_aliases[self.active_session_id] = alias
                save_session_aliases(self.session_aliases)
                self.refresh_sessions_after_alias_update()
                self.set_status("会话别名已更新", "idle")

    def clear_current_session_alias(self) -> None:
                if not self.active_session_id or self.active_session_id not in self.session_aliases:
                    return
                self.session_aliases.pop(self.active_session_id, None)
                save_session_aliases(self.session_aliases)
                self.refresh_sessions_after_alias_update()
                self.set_status("已清除本地别名", "idle")

    def copy_current_session_id(self) -> None:
                if not self.active_session_id:
                    return
                QGuiApplication.clipboard().setText(self.active_session_id)
                self.set_status("已复制会话 ID", "idle")

    def open_current_session_file(self) -> None:
                if not self.active_session_id:
                    return
                session_file = find_session_file(self.config.codex_home, self.active_session_id)
                if not session_file or not session_file.exists():
                    QMessageBox.critical(self, "codex-ui", "未找到当前会话文件。")
                    return
                opener = shutil.which("xdg-open")
                if not opener:
                    QGuiApplication.clipboard().setText(str(session_file))
                    QMessageBox.information(self, "codex-ui", f"未找到 xdg-open，已复制路径：\n{session_file}")
                    return
                try:
                    subprocess.Popen(
                        [opener, str(session_file)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except OSError as exc:
                    QMessageBox.critical(self, "codex-ui", str(exc))
                    return
                self.set_status("", "idle")

    def toggle_pin_active_session(self) -> None:
                if not self.active_session_id:
                    return
                if self.active_session_id in self.pinned_session_ids:
                    self.pinned_session_ids.remove(self.active_session_id)
                    self.set_status("已取消置顶会话", "idle")
                else:
                    self.pinned_session_ids.add(self.active_session_id)
                    self.set_status("已置顶当前会话", "idle")
                save_pinned_session_ids(self.pinned_session_ids)
                self.update_pin_button()
                self.refresh_session_list()
