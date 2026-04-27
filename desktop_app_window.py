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

from desktop_app_window_common import WindowCommonMixin
from desktop_app_window_layout import WindowLayoutMixin
from desktop_app_window_sessions import WindowSessionMixin
from desktop_app_window_accounts import WindowAccountMixin
from desktop_app_window_conversation import WindowConversationMixin

class MainWindow(
    WindowCommonMixin,
    WindowLayoutMixin,
    WindowSessionMixin,
    WindowAccountMixin,
    WindowConversationMixin,
    QMainWindow,
):
    def __init__(self, config: AppConfig) -> None:
                super().__init__()
                self.config = config
                self.codex_auth_path = resolve_codex_auth_path(self.config)
                self.active_account = load_active_account(self.config)
                self.all_accounts = load_all_accounts(self.config)
                self.session_account_map = seed_session_account_map(
                    self.config, load_session_account_map(self.config)
                )
                self.pinned_session_ids = load_pinned_session_ids()
                self.session_aliases = load_session_aliases()
                self.session_work_dir_overrides = load_session_work_dir_overrides()
                self.session_scope = "all"
                self.session_page_size = 60
                self.visible_session_limit = self.session_page_size
                self.session_message_cache: dict[str, ConversationCacheEntry] = {}
                self.active_session_messages: list[ChatMessage] = []
                self.active_message_start_index = 0
                self.initial_message_render_limit = INITIAL_CONVERSATION_RENDER_LIMIT
                self.message_render_chunk_size = CONVERSATION_RENDER_CHUNK_SIZE
                self.sessions = load_sessions(self.config, session_aliases=self.session_aliases)
                self.filtered_sessions = self.sessions[:]
                self.active_session_id: str | None = self.sessions[0].session_id if self.sessions else None
                self.worker: CodexWorker | None = None
                self.workers: dict[str, CodexWorker] = {}
                self.worker_key_aliases: dict[str, str] = {}
                self.session_unread_ids: set[str] = set()
                self.account_worker: AccountActionWorker | None = None
                self.account_dialog: AccountDialog | None = None
                self.streaming_bubble: MessageBubble | None = None
                self.streaming_text = ""
                self.streaming_bubbles: dict[str, MessageBubble] = {}
                self.streaming_texts: dict[str, str] = {}
                self.request_account_key = ""
                self.account_action_restore_key = ""
                self.last_prompt = ""
                self.last_attachments: list[AttachmentInfo] = []
                self.pending_attachments: list[AttachmentInfo] = []
                self.last_error = ""
                self.app_version = load_app_version()
                self.latest_release: ReleaseInfo | None = None
                self.update_check_worker: ReleaseCheckWorker | None = None
                self.new_session_work_dir = self.config.work_dir
                self.new_session_work_dir_overridden = False
                self.status_clear_timer = QTimer(self)
                self.status_clear_timer.setSingleShot(True)
                self.status_clear_timer.timeout.connect(self.clear_status_text)
                self.prompt_templates = [
                    ("代码评审", "请审查当前改动，优先指出 bug、回归风险、边界条件和缺失测试。"),
                    ("修复问题", "请先定位根因，再直接修改代码修复问题，并说明验证结果。"),
                    ("重构优化", "请在不改变行为的前提下重构这部分实现，提升可读性和可维护性。"),
                    ("补测试", "请为当前功能补齐关键测试，覆盖正常路径、边界条件和失败场景。"),
                    ("解释代码", "请结合当前仓库上下文解释这段代码的作用、调用链和关键设计点。"),
                ]

                self.setWindowTitle("Codex for Linux")
                icon = load_app_icon()
                if icon is not None and not icon.isNull():
                    self.setWindowIcon(icon)
                self.resize(1200, 900)
                self.setMinimumSize(1200, 900)

                central = QWidget()
                self.setCentralWidget(central)
                root = QHBoxLayout(central)
                root.setContentsMargins(0, 0, 0, 0)
                root.setSpacing(0)

                self.sidebar = self.build_sidebar()
                self.content = self.build_content()
                root.addWidget(self.sidebar, 0)
                root.addWidget(self.content, 1)

                self.refresh_attachment_widgets()
                self.update_permission_selector()
                self.apply_session_filters()
                self.load_active_session(scroll_to_top=False)
                self.setup_shortcuts()
                self.apply_styles()
                self.update_work_dir_label()
                self.update_version_label()

                self.account_timer = QTimer(self)
                self.account_timer.timeout.connect(self.check_account_change)
                self.account_timer.start(3000)
                QTimer.singleShot(0, self.start_background_release_check)
