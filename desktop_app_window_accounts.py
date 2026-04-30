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

class WindowAccountMixin:
    def update_account_label(self) -> None:
                if not self.active_account:
                    self.account_label.setText("账号未识别")
                    return
                if self.active_account.email:
                    self.account_label.setText(f"当前账号 · {self.active_account.email}")
                else:
                    self.account_label.setText(f"当前账号 · {self.active_account.display_name}")

    def refresh_account_panel(self) -> None:
                self.account_manage_button.setEnabled(self.account_worker is None)
                self.account_manage_button.setText("账号处理中..." if self.account_worker is not None else "账号管理")
                if self.account_dialog is not None and self.account_dialog.isVisible():
                    self.account_dialog.reload()

    def sync_account_state(self, keep_selection: bool = True, refresh_sessions: bool = False) -> None:
                self.active_account = load_active_account(self.config)
                self.all_accounts = load_all_accounts(self.config)
                self.session_account_map = seed_session_account_map(
                    self.config, load_session_account_map(self.config)
                )
                self.update_account_label()
                self.refresh_account_panel()
                if refresh_sessions:
                    self.refresh_sessions_for_account(keep_selection=keep_selection)

    def switch_account(self, account_key: str, label: str) -> None:
                if self.account_worker is not None or self.has_running_worker():
                    return
                if not switch_active_account_local(self.config.codex_home, account_key):
                    QMessageBox.critical(self, "codex-ui", "未找到目标账号，无法切换。")
                    return
                self.sync_account_state(keep_selection=True, refresh_sessions=False)
                self.set_status("", "idle")

    def login_new_account(self) -> None:
                if self.account_worker is not None:
                    return
                if not (self.codex_auth_path and (Path(self.codex_auth_path).exists() or shutil.which(Path(self.codex_auth_path).name))):
                    QMessageBox.critical(self, "codex-ui", "未找到 codex-auth，无法发起新账号登录。")
                    return
                self.set_status("正在打开账号登录流程...", "idle")
                self.account_worker = AccountActionWorker(
                    self.config,
                    self.codex_auth_path,
                    ["login"],
                    "新账号已登录",
                )
                self.account_worker.finished_ok.connect(self.on_account_action_finished)
                self.account_worker.failed.connect(self.on_account_action_failed)
                self.account_worker.finished.connect(self.on_account_worker_thread_finished)
                self.refresh_account_panel()
                self.account_worker.start()

    def reload_accounts(self) -> None:
                self.sync_account_state(keep_selection=True, refresh_sessions=False)
                self.set_status("", "idle")

    def refresh_account_usage(self) -> None:
                if self.account_worker is not None:
                    return
                if not (self.codex_auth_path and (Path(self.codex_auth_path).exists() or shutil.which(Path(self.codex_auth_path).name))):
                    QMessageBox.critical(self, "codex-ui", "未找到 codex-auth，无法刷新账号用量。")
                    return
                self.account_action_restore_key = self.active_account.account_key if self.active_account else ""
                self.account_worker = AccountActionWorker(
                    self.config,
                    self.codex_auth_path,
                    ["list"],
                    "账号用量已刷新",
                )
                self.account_worker.finished_ok.connect(self.on_account_action_finished)
                self.account_worker.failed.connect(self.on_account_action_failed)
                self.account_worker.finished.connect(self.on_account_worker_thread_finished)
                self.refresh_account_panel()
                self.account_worker.start()

    def open_account_dialog(self) -> None:
                self.reload_accounts()
                dialog = AccountDialog(self)
                self.account_dialog = dialog
                dialog.exec()
                self.account_dialog = None

    def open_settings_dialog(self) -> None:
                dialog = SettingsDialog(self)
                dialog.exec()

    def on_account_action_finished(self, message: str) -> None:
                if self.account_action_restore_key:
                    switch_active_account_local(self.config.codex_home, self.account_action_restore_key)
                    self.account_action_restore_key = ""
                self.sync_account_state(keep_selection=True, refresh_sessions=False)
                if message == "新账号已登录":
                    self.set_status(message, "idle")
                else:
                    self.set_status("", "idle")

    def on_account_action_failed(self, error: str) -> None:
                if self.account_action_restore_key:
                    switch_active_account_local(self.config.codex_home, self.account_action_restore_key)
                    self.account_action_restore_key = ""
                    self.sync_account_state(keep_selection=True, refresh_sessions=False)
                self.refresh_account_panel()
                compact_error = " ".join((error or "账号操作失败").split()).strip()
                self.set_status("账号操作失败", "idle")
                QMessageBox.critical(self, "codex-ui", compact_error)

    def on_account_worker_thread_finished(self) -> None:
                worker = self.sender()
                if worker is self.account_worker:
                    self.account_worker.deleteLater()
                    self.account_worker = None
                    self.refresh_account_panel()

    def current_local_account(self) -> LocalAccountInfo | None:
                if not self.active_account:
                    return None
                for account in self.all_accounts:
                    if account.account_key == self.active_account.account_key:
                        return account
                return None

    def apply_runtime_config(self, input_method_changed: bool = False) -> None:
                self.codex_auth_path = resolve_codex_auth_path(self.config)
                if not self.active_session_id and not self.new_session_work_dir_overridden:
                    self.new_session_work_dir = self.config.work_dir
                self.update_work_dir_label()
                self.update_permission_selector()
                self.refresh_account_panel()
                if input_method_changed:
                    self.set_status("设置已保存，输入法策略需重启应用后完全生效", "idle")
                else:
                    self.set_status("设置已保存", "idle")

    def copy_current_account_info(self) -> None:
                account = self.current_local_account()
                if not account:
                    QMessageBox.critical(self, "codex-ui", "当前没有可复制的账号信息。")
                    return
                parts = [f"账号: {account.display_name}"]
                if account.email and account.email != account.display_name:
                    parts.append(f"邮箱: {account.email}")
                if account.plan:
                    parts.append(f"套餐: {account.plan}")
                if account.auth_mode:
                    parts.append(f"认证: {account.auth_mode}")
                parts.append(f"Account Key: {account.account_key}")
                if account.usage_detail:
                    parts.append(f"用量: {account.usage_detail}")
                QGuiApplication.clipboard().setText("\n".join(parts))
                self.set_status("已复制当前账号信息", "idle")

    def open_accounts_directory(self) -> None:
                accounts_dir = self.config.codex_home / "accounts"
                if not accounts_dir.exists():
                    QMessageBox.critical(self, "codex-ui", f"账号目录不存在：{accounts_dir}")
                    return
                opener = shutil.which("xdg-open")
                if not opener:
                    QGuiApplication.clipboard().setText(str(accounts_dir))
                    QMessageBox.information(self, "codex-ui", f"未找到 xdg-open，已复制路径：\n{accounts_dir}")
                    return
                try:
                    subprocess.Popen(
                        [opener, str(accounts_dir)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except OSError as exc:
                    QMessageBox.critical(self, "codex-ui", str(exc))
                    return
                self.set_status("", "idle")

    def bind_session_to_active_account(self, session_id: str | None) -> None:
                if not session_id or not self.active_account:
                    return
                if self.session_account_map.get(session_id) == self.active_account.account_key:
                    return
                self.session_account_map[session_id] = self.active_account.account_key
                save_session_account_map(self.session_account_map)

    def remember_request_account(self) -> None:
                self.request_account_key = self.active_account.account_key if self.active_account else ""

    def restore_request_account(self) -> None:
                account_key = self.request_account_key
                self.request_account_key = ""
                if not account_key:
                    return
                latest = load_active_account(self.config)
                latest_key = latest.account_key if latest else ""
                if latest_key != account_key:
                    switch_active_account_local(self.config.codex_home, account_key)
                self.sync_account_state(keep_selection=True, refresh_sessions=False)

    def mark_session_updated(self, session_id: str | None, raw_time: str | None = None) -> None:
                if not session_id:
                    return
                latest_raw = raw_time or current_local_iso()
                latest_label = to_local_time(latest_raw, "%m-%d %H:%M")
                latest_title = apply_session_alias(session_id, "新会话", self.session_aliases)
                session_found = False
                for session in self.sessions:
                    if session.session_id != session_id:
                        continue
                    latest_title = session.thread_name or latest_title
                    session.thread_name = latest_title
                    session.updated_at_raw = latest_raw
                    session.updated_at = latest_label
                    session_found = True
                    break

                if not session_found:
                    self.sessions.append(
                        SessionSummary(
                            session_id=session_id,
                            thread_name=latest_title,
                            updated_at=latest_label,
                            updated_at_raw=latest_raw,
                        )
                    )

                self.sessions.sort(key=lambda x: session_sort_key(x.updated_at_raw), reverse=True)
                self.apply_session_filters()

    def check_account_change(self) -> None:
                if self.has_running_worker():
                    return
                latest = load_active_account(self.config)
                latest_key = latest.account_key if latest else ""
                current_key = self.active_account.account_key if self.active_account else ""
                if latest_key == current_key:
                    return
                self.sync_account_state(keep_selection=False, refresh_sessions=False)
                self.set_status("", "idle")
