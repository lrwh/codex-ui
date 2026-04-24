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
    QImage,
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

class AccountDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("accountDialog")
        self.setWindowTitle("账号管理")
        self.setModal(True)
        self.resize(520, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("账号管理")
        title.setObjectName("pageTitle")
        subtitle = QLabel("切换当前活跃账号，或发起新的登录流程")
        subtitle.setObjectName("pageSubtitle")

        top = QVBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(2)
        top.addWidget(title)
        top.addWidget(subtitle)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.login_button = QPushButton("登录新账号")
        self.login_button.setObjectName("scopeButton")
        self.refresh_button = QPushButton("刷新用量")
        self.refresh_button.setObjectName("scopeButton")
        self.copy_button = QPushButton("复制当前账号")
        self.copy_button.setObjectName("scopeButton")
        self.open_dir_button = QPushButton("打开账号目录")
        self.open_dir_button.setObjectName("scopeButton")
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("scopeButton")
        self.login_button.clicked.connect(self.on_login)
        self.refresh_button.clicked.connect(self.on_refresh)
        self.copy_button.clicked.connect(self.on_copy_current)
        self.open_dir_button.clicked.connect(self.on_open_dir)
        self.close_button.clicked.connect(self.accept)
        action_row.addWidget(self.login_button, 0)
        action_row.addWidget(self.refresh_button, 0)
        action_row.addWidget(self.copy_button, 0)
        action_row.addWidget(self.open_dir_button, 0)
        action_row.addStretch(1)
        action_row.addWidget(self.close_button, 0)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("sidebarHint")

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)

        root.addLayout(top)
        root.addLayout(action_row)
        root.addWidget(self.hint_label)
        root.addWidget(self.list_host, 1)

        self.reload()

    def clear_rows(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            layout = item.layout()
            if widget:
                widget.deleteLater()
            elif layout:
                self.window.clear_layout_widgets(layout)

    def reload(self) -> None:
        self.window.all_accounts = load_all_accounts(self.window.config)
        self.clear_rows()
        has_auth_tool = bool(
            self.window.codex_auth_path
            and (Path(self.window.codex_auth_path).exists() or shutil.which(Path(self.window.codex_auth_path).name))
        )
        self.login_button.setEnabled(has_auth_tool and self.window.account_worker is None)
        self.refresh_button.setEnabled(self.window.account_worker is None)
        if self.window.account_worker is not None:
            self.hint_label.setText("账号操作进行中…")
        elif not has_auth_tool:
            self.hint_label.setText("未找到 codex-auth，仅支持本地账号切换")
        else:
            self.hint_label.setText(f"本地账号 {len(self.window.all_accounts)} 个")
        self.copy_button.setEnabled(bool(self.window.active_account))
        self.open_dir_button.setEnabled(True)

        for account in self.window.all_accounts:
            row = QFrame()
            row.setObjectName("accountCard")
            row.setProperty("active", account.is_active)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(10)

            text_layout = QVBoxLayout()
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(3)
            title = QLabel(account.display_name)
            title.setObjectName("accountTitle")
            meta_parts = []
            if account.email and account.email != account.display_name:
                meta_parts.append(account.email)
            if account.plan:
                meta_parts.append(account.plan)
            if account.auth_mode:
                meta_parts.append(account.auth_mode)
            if account.last_session_id:
                meta_parts.append(account.last_session_id[:8])
            meta = QLabel(" · ".join(meta_parts))
            meta.setObjectName("accountMeta")
            usage = QLabel()
            usage.setObjectName("accountUsage")
            usage.setTextFormat(Qt.RichText)
            usage.setText(account.usage_summary_html or "<span style='color:#8d7763;'>剩余用量暂不可用</span>")
            text_layout.addWidget(title)
            text_layout.addWidget(meta)
            text_layout.addWidget(usage)

            action = QPushButton("当前" if account.is_active else "切换")
            action.setObjectName("accountCurrentButton" if account.is_active else "accountSwitchButton")
            action.setEnabled((not account.is_active) and self.window.account_worker is None)
            action.clicked.connect(
                lambda _checked=False, account_key=account.account_key, label=account.display_name: self.on_switch(
                    account_key, label
                )
            )

            row_layout.addLayout(text_layout, 1)
            row_layout.addWidget(action, 0)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

    def on_switch(self, account_key: str, label: str) -> None:
        self.window.switch_account(account_key, label)
        self.accept()

    def on_login(self) -> None:
        self.window.login_new_account()
        self.accept()

    def on_refresh(self) -> None:
        self.window.refresh_account_usage()

    def on_copy_current(self) -> None:
        self.window.copy_current_account_info()

    def on_open_dir(self) -> None:
        self.window.open_accounts_directory()


class ModelSelectionDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.selected_model = window.config.model
        self.selected_reasoning_effort = window.config.model_reasoning_effort
        self.setObjectName("accountDialog")
        self.setWindowTitle("选择模型")
        self.setModal(True)
        self.resize(420, 430)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("选择模型")
        title.setObjectName("pageTitle")
        subtitle = QLabel("用于后续发送；默认会跟随 Codex CLI 配置")
        subtitle.setObjectName("pageSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.model_list = QListWidget()
        self.model_list.setObjectName("sessionList")
        self.model_list.setFrameShape(QFrame.NoFrame)
        self.model_list.setSpacing(4)
        current = (window.config.model or "").strip()
        for model in model_choices(current):
            label = "默认（Codex CLI 配置）" if not model else model
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, model)
            self.model_list.addItem(item)
            if model == current:
                self.model_list.setCurrentItem(item)
        self.model_list.itemDoubleClicked.connect(lambda _item: self.on_save())
        self.model_list.currentItemChanged.connect(self.on_current_model_changed)
        root.addWidget(self.model_list, 1)

        custom_label = QLabel("自定义模型")
        custom_label.setObjectName("cardMeta")
        self.custom_model_input = QLineEdit(current)
        self.custom_model_input.setObjectName("searchInput")
        self.custom_model_input.setPlaceholderText("例如 gpt-5.4")
        root.addWidget(custom_label)
        root.addWidget(self.custom_model_input)

        effort_label = QLabel("推理强度")
        effort_label.setObjectName("cardMeta")
        self.reasoning_effort_combo = QComboBox()
        self.reasoning_effort_combo.setObjectName("searchInput")
        current_effort = normalize_reasoning_effort(window.config.model_reasoning_effort)
        for label, value in DEFAULT_REASONING_EFFORT_CHOICES:
            item_label = "默认（Codex CLI 配置）" if not value else label
            self.reasoning_effort_combo.addItem(item_label, value)
        effort_index = self.reasoning_effort_combo.findData(current_effort)
        if effort_index >= 0:
            self.reasoning_effort_combo.setCurrentIndex(effort_index)
        root.addWidget(effort_label)
        root.addWidget(self.reasoning_effort_combo)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        cancel = QPushButton("取消")
        cancel.setObjectName("scopeButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("应用")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.on_save)
        action_row.addStretch(1)
        action_row.addWidget(cancel, 0)
        action_row.addWidget(save, 0)
        root.addLayout(action_row)

    def on_current_model_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        self.custom_model_input.setText(str(current.data(Qt.UserRole) or ""))

    def on_save(self) -> None:
        self.selected_model = self.custom_model_input.text().strip()
        self.selected_reasoning_effort = normalize_reasoning_effort(self.reasoning_effort_combo.currentData())
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("accountDialog")
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(520, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        subtitle = QLabel("修改新会话默认参数和客户端运行配置")
        subtitle.setObjectName("pageSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.work_dir_input = QLineEdit(str(window.config.work_dir))
        self.work_dir_input.setObjectName("searchInput")
        self.codex_path_input = QLineEdit(window.config.codex_path)
        self.codex_path_input.setObjectName("searchInput")

        self.permission_mode_combo = QComboBox()
        self.permission_mode_combo.addItem("工作区", "workspace")
        self.permission_mode_combo.addItem("只读", "readonly")
        self.permission_mode_combo.addItem("全权限", "full")
        self.permission_mode_combo.setCurrentIndex(
            self.permission_mode_combo.findData(
                permission_preset_from_runtime(window.config.approval_policy, window.config.sandbox_mode)
            )
        )

        self.skip_git_check = QCheckBox("跳过 git 仓库检查")
        self.skip_git_check.setChecked(window.config.skip_git_repo_check)

        self.input_method_combo = QComboBox()
        self.input_method_combo.addItem("auto", "auto")
        self.input_method_combo.addItem("system", "system")
        self.input_method_combo.addItem("fcitx", "fcitx")
        self.input_method_combo.addItem("ibus", "ibus")
        self.input_method_combo.addItem("xim", "xim")
        self.input_method_combo.setCurrentText(window.config.input_method_strategy)

        fields = [
            ("工作目录", self.work_dir_input),
            ("权限模式", self.permission_mode_combo),
            ("Codex 路径", self.codex_path_input),
            ("输入法策略", self.input_method_combo),
        ]
        for label_text, widget in fields:
            label = QLabel(label_text)
            label.setObjectName("cardMeta")
            root.addWidget(label)
            root.addWidget(widget)

        root.addWidget(self.skip_git_check)

        hint = QLabel("权限模式会同步到输入区的权限选择；输入法策略保存后需重启应用才能完全生效。")
        hint.setObjectName("sidebarHint")
        root.addWidget(hint)
        root.addStretch(1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        cancel = QPushButton("取消")
        cancel.setObjectName("scopeButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.on_save)
        action_row.addStretch(1)
        action_row.addWidget(cancel, 0)
        action_row.addWidget(save, 0)
        root.addLayout(action_row)

    def on_save(self) -> None:
        work_dir = Path(self.work_dir_input.text().strip()).expanduser()
        if not work_dir.exists() or not work_dir.is_dir():
            QMessageBox.critical(self, "codex-ui", f"工作目录不存在：{work_dir}")
            return

        codex_path = self.codex_path_input.text().strip() or "codex"
        old_input_strategy = self.window.config.input_method_strategy
        approval_policy, sandbox_mode = runtime_from_permission_preset(self.permission_mode_combo.currentData())
        self.window.config = AppConfig(
            codex_path=codex_path,
            codex_home=self.window.config.codex_home,
            work_dir=work_dir,
            model=self.window.config.model,
            model_reasoning_effort=self.window.config.model_reasoning_effort,
            full_auto=(approval_policy == "on-request" and sandbox_mode == "workspace-write"),
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
            skip_git_repo_check=self.skip_git_check.isChecked(),
            recent_session_limit=self.window.config.recent_session_limit,
            input_method_strategy=normalize_input_method_strategy(self.input_method_combo.currentText()),
        )
        save_config(self.window.config)
        self.window.apply_runtime_config(input_method_changed=old_input_strategy != self.window.config.input_method_strategy)
        self.accept()

class SessionGroupHeader(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("sessionGroupHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 2)
        label = QLabel(title)
        label.setObjectName("sessionGroupTitle")
        layout.addWidget(label)


class SessionListItem(QFrame):
    def __init__(self, session: SessionSummary, selected: bool, query: str = "") -> None:
        super().__init__()
        self.setObjectName("sessionCard")
        self.setProperty("selected", selected)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        dot = QLabel("●")
        dot.setObjectName("sessionDot")
        dot.setProperty("selected", selected)

        short_id = session.session_id[:8]
        title = QLabel(highlight_match(truncate_text(session.thread_name, 18), query))
        title.setObjectName("sessionTitle")
        title.setWordWrap(False)
        title.setTextFormat(Qt.RichText)

        top_row.addWidget(dot, 0, Qt.AlignTop)
        top_row.addWidget(title, 1)

        meta = QLabel(f"{session.updated_at} · {highlight_match(short_id, query)}")
        meta.setObjectName("sessionMeta")
        meta.setTextFormat(Qt.RichText)

        layout.addLayout(top_row)
        layout.addWidget(meta)


class MessageBubble(QFrame):
    def __init__(self, message: ChatMessage) -> None:
        super().__init__()
        self.message = message
        self.setObjectName("bubbleUser" if message.role == "user" else "bubbleAssistant")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        bubble = QFrame()
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 12, 16, 12)
        bubble_layout.setSpacing(6)
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bubble.setMaximumWidth(920)
        bubble.setMinimumWidth(700 if message.role == "assistant" else 520)

        header = QLabel(("你" if message.role == "user" else "Codex") + f"  {message.timestamp}")
        header.setObjectName("bubbleHeader")
        self.body = QLabel()
        self.body.setObjectName("bubbleBody")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setOpenExternalLinks(True)
        self.body.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard
        )
        self.body.setText(render_markdown_html(message.text))
        bubble_layout.addWidget(header)
        bubble_layout.addWidget(self.body)

        if message.role == "user":
            root.addStretch(1)
            root.addWidget(bubble, 0)
        else:
            root.addWidget(bubble, 0)
            root.addStretch(1)
        bubble.setObjectName("bubbleCardUser" if message.role == "user" else "bubbleCardAssistant")

    def update_text(self, text: str) -> None:
        self.message.text = text
        self.body.setText(render_markdown_html(text))

    def minimumSizeHint(self):
        return self.sizeHint()


class ComposerInput(QPlainTextEdit):
    command_requested = Signal()
    attachments_pasted = Signal(list)
    clipboard_image_pasted = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(False)
        self.setTabChangesFocus(False)

    def canInsertFromMimeData(self, source) -> bool:
        if self._extract_local_file_paths(source):
            return True
        if source is not None and source.hasImage():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        file_paths = self._extract_local_file_paths(source)
        if file_paths:
            self.attachments_pasted.emit(file_paths)
            return
        if source is not None and source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self.clipboard_image_pasted.emit(image)
                return
        super().insertFromMimeData(source)

    def keyPressEvent(self, event) -> None:
        stripped = self.toPlainText().strip()
        command = stripped.split(None, 1)[0].lower() if stripped else ""
        if (
            event.key() in {Qt.Key_Return, Qt.Key_Enter}
            and not (event.modifiers() & Qt.ControlModifier)
            and command == "/model"
        ):
            self.command_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _extract_local_file_paths(self, source) -> list[str]:
        if source is None or not source.hasUrls():
            return []
        paths: list[str] = []
        for url in source.urls():
            if not url.isLocalFile():
                continue
            local_path = url.toLocalFile().strip()
            if local_path:
                paths.append(local_path)
        return paths

