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

class WindowLayoutMixin:
    def setup_shortcuts(self) -> None:
                self.shortcuts: list[QShortcut] = []
                bindings = [
                    ("Ctrl+N", self.new_session),
                    ("Ctrl+Return", self.send_prompt),
                    ("Ctrl+Enter", self.send_prompt),
                    ("Esc", self.stop_current_request),
                ]
                for sequence, handler in bindings:
                    shortcut = QShortcut(QKeySequence(sequence), self)
                    shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                    shortcut.activated.connect(handler)
                    self.shortcuts.append(shortcut)

    def build_sidebar(self) -> QWidget:
                panel = QFrame()
                panel.setObjectName("sidebar")
                panel.setFixedWidth(252)
                layout = QVBoxLayout(panel)
                layout.setContentsMargins(14, 14, 14, 14)
                layout.setSpacing(10)

                brand_row = QHBoxLayout()
                brand_row.setContentsMargins(0, 0, 0, 0)
                brand_row.setSpacing(12)

                badge = QLabel("C")
                badge.setObjectName("sidebarBadge")

                brand_stack = QVBoxLayout()
                brand_stack.setContentsMargins(0, 0, 0, 0)
                brand_stack.setSpacing(2)

                title = QLabel("Codex for Linux")
                title.setObjectName("sidebarTitle")
                meta = QLabel("Linux 桌面客户端")
                meta.setObjectName("sidebarMeta")
                brand_stack.addWidget(title)
                brand_stack.addWidget(meta)
                brand_row.addWidget(badge, 0, Qt.AlignTop)
                brand_row.addLayout(brand_stack, 1)

                self.work_dir_label = QLabel("")
                self.work_dir_label.setObjectName("cardMeta")

                self.account_label = QLabel("")
                self.account_label.setObjectName("sidebarAccount")
                self.update_account_label()
                account_action_row = QHBoxLayout()
                account_action_row.setContentsMargins(0, 0, 0, 0)
                account_action_row.setSpacing(8)
                self.account_manage_button = QPushButton("账号管理")
                self.account_manage_button.setObjectName("scopeButton")
                self.account_manage_button.clicked.connect(self.open_account_dialog)
                self.settings_button = QPushButton("设置")
                self.settings_button.setObjectName("scopeButton")
                self.settings_button.clicked.connect(self.open_settings_dialog)
                account_action_row.addWidget(self.account_manage_button, 0)
                account_action_row.addWidget(self.settings_button, 0)
                account_action_row.addStretch(1)

                section_label = QLabel("会话")
                section_label.setObjectName("sidebarSection")

                scope_row = QHBoxLayout()
                scope_row.setContentsMargins(0, 0, 0, 0)
                scope_row.setSpacing(8)
                self.scope_all_button = QPushButton("全部")
                self.scope_all_button.setObjectName("scopeButton")
                self.scope_all_button.clicked.connect(lambda: self.set_session_scope("all"))
                self.scope_pinned_button = QPushButton("置顶")
                self.scope_pinned_button.setObjectName("scopeButton")
                self.scope_pinned_button.clicked.connect(lambda: self.set_session_scope("pinned"))
                self.scope_recent_button = QPushButton("最近7天")
                self.scope_recent_button.setObjectName("scopeButton")
                self.scope_recent_button.clicked.connect(lambda: self.set_session_scope("recent"))
                scope_row.addWidget(self.scope_all_button, 0)
                scope_row.addWidget(self.scope_pinned_button, 0)
                scope_row.addWidget(self.scope_recent_button, 0)
                scope_row.addStretch(1)

                self.search = QLineEdit()
                self.search.setObjectName("searchInput")
                self.search.setPlaceholderText("搜索标题或 ID")
                self.search.textChanged.connect(self.on_search)
                self.session_list = QListWidget()
                self.session_list.setObjectName("sessionList")
                self.session_list.currentRowChanged.connect(self.on_session_selected)
                self.session_list.setFrameShape(QFrame.NoFrame)
                self.session_list.setSpacing(4)
                self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                self.session_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                self.session_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
                self.load_more_button = QPushButton("加载更多")
                self.load_more_button.setObjectName("scopeButton")
                self.load_more_button.clicked.connect(self.load_more_sessions)

                layout.addLayout(brand_row)
                layout.addWidget(self.account_label)
                layout.addLayout(account_action_row)
                layout.addSpacing(4)
                layout.addWidget(section_label)
                layout.addLayout(scope_row)
                layout.addWidget(self.search)
                layout.addWidget(self.session_list, 1)
                layout.addWidget(self.load_more_button, 0, Qt.AlignLeft)
                return panel

    def build_content(self) -> QWidget:
                page = QWidget()
                page.setObjectName("page")
                root = QVBoxLayout(page)
                root.setContentsMargins(14, 12, 14, 12)
                root.setSpacing(6)

                top_title = QFrame()
                top_title.setObjectName("topTitleCard")
                top_title_layout = QGridLayout(top_title)
                top_title_layout.setContentsMargins(0, 0, 0, 0)
                top_title_layout.setHorizontalSpacing(10)
                top_title_layout.setVerticalSpacing(0)
                top_title_layout.setColumnStretch(0, 1)
                top_title_layout.setColumnStretch(1, 2)
                top_title_layout.setColumnStretch(2, 1)
                self.status_label = QLabel("")
                self.status_label.setObjectName("statusText")
                self.status_label.setAlignment(Qt.AlignCenter)
                top_title_layout.addWidget(self.status_label, 0, 1, 1, 1, Qt.AlignCenter)

                inner = QWidget()
                inner.setObjectName("contentInner")
                inner_layout = QHBoxLayout(inner)
                inner_layout.setContentsMargins(0, 0, 0, 0)
                inner_layout.setSpacing(10)

                main_column = QWidget()
                main_column.setObjectName("mainColumn")
                main_layout = QVBoxLayout(main_column)
                main_layout.setContentsMargins(0, 0, 0, 0)
                main_layout.setSpacing(8)

                self.header_card = self.make_card("当前会话", "headerCard")
                self.header_title = QLabel("-")
                self.header_title.setObjectName("cardHeadline")
                self.header_meta = QLabel("")
                self.header_meta.setObjectName("cardMeta")
                self.header_status = QLabel("idle")
                self.header_status.setObjectName("statusChip")
                self.rename_session_button = self.make_scope_button("重命名", self.rename_current_session)
                self.copy_session_id_button = self.make_scope_button("复制 ID", self.copy_current_session_id)
                self.open_session_file_button = self.make_scope_button("打开文件", self.open_current_session_file)
                self.clear_session_alias_button = self.make_scope_button("清别名", self.clear_current_session_alias)
                self.edit_work_dir_button = self.make_scope_button("改路径", self.edit_current_work_dir)
                self.pin_button = QPushButton("置顶")
                self.pin_button.setObjectName("pinButton")
                self.pin_button.clicked.connect(self.toggle_pin_active_session)
                session_action_row = QHBoxLayout()
                session_action_row.setContentsMargins(0, 0, 0, 0)
                session_action_row.setSpacing(8)
                session_action_row.addWidget(self.rename_session_button, 0)
                session_action_row.addWidget(self.copy_session_id_button, 0)
                session_action_row.addWidget(self.open_session_file_button, 0)
                session_action_row.addWidget(self.clear_session_alias_button, 0)
                session_action_row.addWidget(self.edit_work_dir_button, 0)
                header_row = QHBoxLayout()
                header_row.setContentsMargins(0, 0, 0, 0)
                header_row.setSpacing(10)
                header_row.addWidget(self.work_dir_label, 0)
                header_row.addLayout(session_action_row, 0)
                header_row.addStretch(1)
                header_row.addWidget(self.pin_button, 0)
                header_row.addWidget(self.header_meta, 0)
                header_row.addWidget(self.header_status, 0, Qt.AlignRight)
                self.header_title_row.insertWidget(1, self.header_title, 0)
                self.header_card.layout().addLayout(header_row)
                self.resume_command = QLineEdit()
                self.resume_command.setObjectName("resumeCommand")
                self.resume_command.setReadOnly(True)
                self.resume_command.setFocusPolicy(Qt.ClickFocus)
                self.copy_resume_button = QPushButton("复制")
                self.copy_resume_button.setObjectName("copyButton")
                self.copy_resume_button.clicked.connect(self.copy_resume_command)
                resume_hint = QLabel("手动恢复")
                resume_hint.setObjectName("cardMeta")
                resume_row = QHBoxLayout()
                resume_row.setContentsMargins(0, 0, 0, 0)
                resume_row.setSpacing(8)
                resume_row.addWidget(resume_hint, 0)
                resume_row.addWidget(self.resume_command, 1)
                resume_row.addWidget(self.copy_resume_button, 0)
                self.permission_combo = QComboBox()
                self.permission_combo.setObjectName("permissionSelect")
                self.permission_combo.addItem("工作区", "workspace")
                self.permission_combo.addItem("只读", "readonly")
                self.permission_combo.addItem("全权限", "full")
                self.permission_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                self.permission_combo.setMinimumContentsLength(6)
                self.permission_combo.setFixedWidth(132)
                self.permission_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                self.permission_combo.currentIndexChanged.connect(self.on_permission_preset_changed)
                self.header_card.layout().addLayout(resume_row)

                self.conversation_panel = QFrame()
                self.conversation_panel.setObjectName("conversationPanel")
                self.conversation_panel.setMinimumHeight(0)
                self.conversation_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
                conversation_layout = QVBoxLayout(self.conversation_panel)
                conversation_layout.setContentsMargins(0, 0, 0, 0)
                conversation_layout.setSpacing(0)

                self.chat_card = self.make_card("对话内容", "chatCard")
                self.message_count_label = QLabel("")
                self.message_count_label.setObjectName("cardMeta")
                self.load_more_messages_button = self.make_scope_button("加载更早消息", self.load_older_messages)
                self.chat_title_row.insertWidget(1, self.message_count_label, 0)
                self.chat_title_row.addWidget(self.load_more_messages_button, 0)
                self.chat_scroll = QScrollArea()
                self.chat_scroll.setObjectName("chatScroll")
                self.chat_scroll.setWidgetResizable(True)
                self.chat_scroll.setFrameShape(QFrame.NoFrame)
                self.chat_scroll.setMinimumHeight(240)
                self.chat_scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
                self.chat_host = QWidget()
                self.chat_host.setObjectName("chatHost")
                self.chat_host_layout = QVBoxLayout(self.chat_host)
                self.chat_host_layout.setContentsMargins(0, 0, 0, 0)
                self.chat_host_layout.setSpacing(0)
                self.chat_host_layout.addStretch(1)
                self.chat_messages_host = QWidget()
                self.chat_messages_host.setObjectName("chatMessagesHost")
                self.chat_scroll.viewport().setObjectName("chatViewport")
                self.chat_layout = QVBoxLayout(self.chat_messages_host)
                self.chat_layout.setContentsMargins(8, 8, 8, 8)
                self.chat_layout.setSpacing(10)
                self.chat_layout.setAlignment(Qt.AlignTop)
                self.chat_host_layout.addWidget(self.chat_messages_host, 0)
                self.chat_scroll.setWidget(self.chat_host)
                self.chat_card.layout().addWidget(self.chat_scroll)
                self.chat_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

                divider = QFrame()
                divider.setObjectName("conversationDivider")
                divider.setFixedHeight(1)

                self.input_card = self.make_card("输入", "inputCard")
                self.input_card.setMinimumHeight(264)
                self.input_card.setMaximumHeight(340)
                self.input_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
                self.request_state_label = QLabel("")
                self.request_state_label.setObjectName("requestStateIdle")
                self.request_state_label.setMinimumHeight(22)
                self.request_state_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                self.input_title_row.insertWidget(1, self.request_state_label, 0)
                self.request_error_label = QLabel("")
                self.request_error_label.setObjectName("requestError")
                self.request_error_label.setWordWrap(True)
                self.request_error_label.hide()
                self.retry_button = QPushButton("重试上次发送")
                self.retry_button.setObjectName("retryButton")
                self.retry_button.clicked.connect(self.retry_last_prompt)
                self.retry_button.hide()
                feedback_row = QHBoxLayout()
                feedback_row.setContentsMargins(0, 0, 0, 0)
                feedback_row.setSpacing(8)
                feedback_row.addStretch(1)
                feedback_row.addWidget(self.retry_button, 0)
                self.input_box = ComposerInput()
                self.input_box.setObjectName("composerBox")
                self.input_box.setPlaceholderText("输入提示词，继续当前会话或直接开启新话题")
                self.input_box.setAttribute(Qt.WA_InputMethodEnabled, True)
                self.input_box.setInputMethodHints(Qt.ImhMultiLine)
                self.input_box.setFixedHeight(120)
                self.input_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                self.input_box.command_requested.connect(self.send_prompt)
                self.input_box.attachments_pasted.connect(self.add_pasted_attachments)
                self.input_box.clipboard_image_pasted.connect(self.add_clipboard_image_attachment)
                template_row = QHBoxLayout()
                template_row.setContentsMargins(0, 0, 0, 0)
                template_row.setSpacing(8)
                template_label = QLabel("快捷指令")
                template_label.setObjectName("cardMeta")
                template_row.addWidget(template_label, 0)
                self.add_attachment_button = self.make_scope_button("添加附件", self.pick_attachments)
                template_row.addWidget(self.add_attachment_button, 0)
                for label, template in self.prompt_templates:
                    button = self.make_scope_button(
                        label,
                        lambda _checked=False, content=template: self.insert_prompt_template(content)
                    )
                    template_row.addWidget(button, 0)
                template_row.addStretch(1)
                attachment_row = QHBoxLayout()
                attachment_row.setContentsMargins(0, 0, 0, 0)
                attachment_row.setSpacing(8)
                attachment_label_widget = QLabel("附件")
                attachment_label_widget.setObjectName("cardMeta")
                self.attachment_hint = QLabel("未添加")
                self.attachment_hint.setObjectName("cardMeta")
                self.attachment_list_host = QWidget()
                self.attachment_list_layout = QHBoxLayout(self.attachment_list_host)
                self.attachment_list_layout.setContentsMargins(0, 0, 0, 0)
                self.attachment_list_layout.setSpacing(6)
                attachment_row.addWidget(attachment_label_widget, 0)
                attachment_row.addWidget(self.attachment_list_host, 1)
                attachment_row.addWidget(self.attachment_hint, 0)
                self.send_button = QPushButton("发送")
                self.send_button.setObjectName("primaryButton")
                self.send_button.clicked.connect(self.send_prompt)
                self.stop_button = QPushButton("停止")
                self.stop_button.setObjectName("stopButton")
                self.stop_button.clicked.connect(self.stop_current_request)
                self.stop_button.hide()
                self.new_button = QPushButton("新会话")
                self.new_button.setObjectName("ghostButton")
                self.new_button.clicked.connect(self.new_session)
                permission_label = QLabel("权限")
                permission_label.setObjectName("cardMeta")
                button_row = QHBoxLayout()
                button_row.setContentsMargins(0, 0, 0, 0)
                button_row.setSpacing(10)
                button_row.addWidget(permission_label, 0)
                button_row.addWidget(self.permission_combo, 0)
                button_row.addStretch(1)
                button_row.addWidget(self.new_button)
                button_row.addWidget(self.stop_button)
                button_row.addWidget(self.send_button)
                self.input_card.layout().addLayout(feedback_row)
                self.input_card.layout().addWidget(self.request_error_label)
                self.input_card.layout().addLayout(template_row)
                self.input_card.layout().addLayout(attachment_row)
                self.input_card.layout().addWidget(self.input_box)
                self.input_card.layout().addLayout(button_row)

                conversation_layout.addWidget(self.chat_card, 1)
                conversation_layout.addWidget(divider)
                conversation_layout.addWidget(self.input_card, 0)
                conversation_layout.setStretch(0, 1)

                self.status_card = QFrame()
                self.status_card.setObjectName("statusBar")
                status_layout = QHBoxLayout(self.status_card)
                status_layout.setContentsMargins(14, 10, 14, 10)
                status_layout.setSpacing(14)
                self.usage_label = QLabel("in 0 · cache 0 · out 0")
                self.usage_label.setObjectName("cardMeta")
                self.help_label = QLabel("搜索 /  /model 选模型/推理  ·  Ctrl+Enter 发送  ·  Ctrl+N 新会话")
                self.help_label.setObjectName("cardMeta")
                status_layout.addWidget(self.usage_label, 0)
                status_layout.addStretch(1)
                status_layout.addWidget(self.help_label, 0, Qt.AlignRight)

                root.addWidget(top_title)
                main_layout.addWidget(self.header_card, 0)
                main_layout.addWidget(self.conversation_panel, 1)
                main_layout.addWidget(self.status_card, 0)
                inner_layout.addWidget(main_column, 1)
                root.addWidget(inner, 1)
                return page

    def make_scope_button(self, text: str, handler) -> QPushButton:
                button = QPushButton(text)
                button.setObjectName("scopeButton")
                button.clicked.connect(handler)
                return button

    def make_card(self, title: str, object_name: str = "card") -> QFrame:
                card = QFrame()
                card.setObjectName(object_name)
                layout = QVBoxLayout(card)
                if object_name == "headerCard":
                    layout.setContentsMargins(14, 8, 14, 10)
                    layout.setSpacing(4)
                elif object_name == "inputCard":
                    layout.setContentsMargins(14, 8, 14, 6)
                    layout.setSpacing(6)
                else:
                    layout.setContentsMargins(14, 12, 14, 12)
                    layout.setSpacing(8)
                if object_name == "headerCard":
                    title_row = QHBoxLayout()
                    title_row.setContentsMargins(0, 0, 0, 0)
                    title_row.setSpacing(10)
                    label = QLabel(title)
                    label.setObjectName("cardTitle")
                    self.header_title_row = title_row
                    title_row.addWidget(label, 0)
                    title_row.addStretch(1)
                    layout.addLayout(title_row)
                elif object_name == "inputCard":
                    title_row = QHBoxLayout()
                    title_row.setContentsMargins(0, 0, 0, 0)
                    title_row.setSpacing(10)
                    label = QLabel(title)
                    label.setObjectName("cardTitle")
                    hint = QLabel("Enter 换行，点击右侧按钮发送")
                    hint.setObjectName("cardMeta")
                    self.input_title_row = title_row
                    title_row.addWidget(label, 0)
                    title_row.addStretch(1)
                    title_row.addWidget(hint, 0, Qt.AlignRight)
                    layout.addLayout(title_row)
                elif object_name == "chatCard":
                    title_row = QHBoxLayout()
                    title_row.setContentsMargins(0, 0, 0, 0)
                    title_row.setSpacing(10)
                    label = QLabel(title)
                    label.setObjectName("cardTitle")
                    self.chat_title_row = title_row
                    title_row.addWidget(label, 0)
                    title_row.addStretch(1)
                    layout.addLayout(title_row)
                else:
                    label = QLabel(title)
                    label.setObjectName("cardTitle")
                    layout.addWidget(label)
                return card

    def apply_styles(self) -> None:
                app = QApplication.instance()
                if app is not None:
                    default_font = app.font()
                    default_font.setPointSize(12)
                    app.setFont(default_font)
                self.setStyleSheet(
                    """
                    QMainWindow, QWidget#page {
                      background: #fcfbf8;
                      color: #2f241c;
                    }
                    QDialog#accountDialog {
                      background: #fcfbf8;
                      color: #2f241c;
                    }
                    QWidget#contentInner {
                      background: transparent;
                    }
                    QFrame#topTitleCard {
                      background: transparent;
                    }
                    QLabel#pageTitle {
                      font-size: 19px;
                      font-weight: 800;
                      color: #2f261f;
                    }
                    QLabel#pageSubtitle {
                      color: #86715d;
                      font-size: 14px;
                    }
                    QFrame#sidebar {
                      background: #fffdfa;
                      border-right: 1px solid #eadfce;
                    }
                    QLabel#sidebarBadge {
                      min-width: 36px;
                      max-width: 36px;
                      min-height: 36px;
                      max-height: 36px;
                      background: #2f7b68;
                      color: white;
                      border-radius: 12px;
                      font-size: 18px;
                      font-weight: 800;
                      qproperty-alignment: AlignCenter;
                    }
                    QLabel#sidebarTitle {
                      font-size: 18px;
                      font-weight: 800;
                      color: #241d17;
                    }
                    QLabel#sidebarMeta {
                      color: #8a7561;
                      font-size: 12px;
                      font-weight: 600;
                    }
                    QLabel#sidebarPath {
                      color: #9b8671;
                      font-size: 11px;
                    }
                    QLabel#sidebarAccount {
                      color: #2f7b68;
                      font-size: 11px;
                      font-weight: 600;
                    }
                    QLabel#sidebarHint {
                      color: #8f7b67;
                      font-size: 11px;
                    }
                    QPushButton#scopeButton {
                      background: #eef5f1;
                      color: #2f7b68;
                      border: 1px solid #d9e8e1;
                      border-radius: 10px;
                      padding: 4px 9px;
                      font-size: 11px;
                      font-weight: 700;
                    }
                    QPushButton#scopeButton:hover {
                      background: #e6f1ec;
                    }
                    QPushButton#scopeButton[selected="true"] {
                      background: #2f7b68;
                      color: white;
                      border-color: #2f7b68;
                    }
                    QLabel#sidebarSection {
                      color: #bc6f2f;
                      font-size: 13px;
                      font-weight: 700;
                    }
                    QFrame#accountCard {
                      background: #fffaf4;
                      border: 1px solid #eadfce;
                      border-radius: 12px;
                    }
                    QFrame#accountCard[active="true"] {
                      background: #f6efe2;
                      border-color: #d9c7ae;
                    }
                    QLabel#accountTitle {
                      color: #2c241d;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QLabel#accountMeta {
                      color: #8d7763;
                      font-size: 10px;
                    }
                    QLabel#accountUsage {
                      color: #6f5b49;
                      font-size: 10px;
                    }
                    QPushButton#accountSwitchButton {
                      background: #eef5f1;
                      color: #2f7b68;
                      border: 1px solid #d9e8e1;
                      border-radius: 10px;
                      padding: 4px 10px;
                      font-size: 11px;
                      font-weight: 700;
                      min-width: 44px;
                    }
                    QPushButton#accountSwitchButton:hover {
                      background: #e4f0ea;
                    }
                    QPushButton#accountCurrentButton {
                      background: #efe5d7;
                      color: #8a613a;
                      border: none;
                      border-radius: 10px;
                      padding: 4px 10px;
                      font-size: 11px;
                      font-weight: 700;
                      min-width: 44px;
                    }
                    QLineEdit#searchInput {
                      border: 1px solid #e1d3c2;
                      border-radius: 12px;
                      padding: 7px 10px;
                      background: white;
                      color: #3b2f26;
                    }
                    QLineEdit#searchInput:focus {
                      border: 1px solid #d28a4a;
                    }
                    QListWidget#sessionList {
                      background: transparent;
                      border: none;
                      outline: none;
                    }
                    QListWidget#sessionList::item {
                      border: none;
                      margin: 0;
                      padding: 0;
                    }
                    QScrollBar:vertical {
                      background: transparent;
                      width: 8px;
                      margin: 6px 0 6px 0;
                    }
                    QScrollBar::handle:vertical {
                      background: #dbcbb8;
                      border-radius: 4px;
                      min-height: 24px;
                    }
                    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                      height: 0px;
                    }
                    QFrame#sessionCard {
                      background: transparent;
                      border: none;
                      border-radius: 12px;
                    }
                    QFrame#sessionGroupHeader {
                      background: transparent;
                      border: none;
                    }
                    QLabel#sessionGroupTitle {
                      color: #a78363;
                      font-size: 11px;
                      font-weight: 700;
                      letter-spacing: 1px;
                    }
                    QFrame#sessionCard[selected="true"] {
                      background: #f7efe2;
                      border-left: 3px solid #2f7b68;
                    }
                    QFrame#sessionCard:hover {
                      background: #faf4eb;
                    }
                    QLabel#sessionTitle {
                      font-size: 13px;
                      font-weight: 800;
                      color: #2c241d;
                    }
                    QLabel#sessionMeta {
                      font-size: 11px;
                      color: #8d7763;
                    }
                    QLabel#sessionDot {
                      color: #d7bf9b;
                      font-size: 11px;
                    }
                    QLabel#sessionDot[selected="true"] {
                      color: #2f7b68;
                    }
                    QLabel#sessionDot[state="running"] {
                      color: #2f7b68;
                    }
                    QLabel#sessionDot[state="unread"] {
                      color: #c98235;
                    }
                    QFrame#headerCard, QFrame#statusBar, QFrame#conversationPanel {
                      background: #fffdfa;
                      border: 1px solid #eadfce;
                      border-radius: 18px;
                    }
                    QFrame#chatCard, QFrame#inputCard {
                      background: transparent;
                      border: none;
                    }
                    QFrame#conversationDivider {
                      background: #efe3d2;
                      border: none;
                      margin: 0 14px 0 14px;
                    }
                    QLabel#cardTitle {
                      font-size: 12px;
                      font-weight: 700;
                      color: #c7742f;
                    }
                    QLabel#cardHeadline {
                      font-size: 16px;
                      font-weight: 700;
                      color: #2d241d;
                    }
                    QLabel#cardMeta {
                      color: #7f6b59;
                      font-size: 12px;
                    }
                    QLabel#cardMetaStrong {
                      color: #5e4c3c;
                      font-size: 15px;
                      font-weight: 600;
                    }
                    QLabel#statusChip {
                      background: #e2f1ea;
                      color: #225e52;
                      border-radius: 11px;
                      padding: 5px 10px;
                      font-weight: 700;
                    }
                    QPushButton#pinButton {
                      background: #f6efe2;
                      color: #8a613a;
                      border: 1px solid #eadfce;
                      border-radius: 11px;
                      padding: 5px 10px;
                      font-weight: 700;
                    }
                    QPushButton#pinButton:hover {
                      background: #efe3d2;
                    }
                    QPushButton#pinButton:disabled {
                      color: #b39a83;
                      background: #faf6ef;
                    }
                    QLabel#requestStateIdle {
                      color: #7f6b59;
                      font-size: 12px;
                    }
                    QLabel#requestStateRunning {
                      color: #2f7b68;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QLabel#requestStateFailed {
                      color: #b0523a;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QLabel#requestError {
                      color: #9b5d45;
                      background: #fff5ee;
                      border: 1px solid #f1d7c8;
                      border-radius: 10px;
                      padding: 6px 8px;
                      font-size: 11px;
                    }
                    QPushButton#retryButton {
                      background: #f6efe2;
                      color: #8a613a;
                      border: 1px solid #eadfce;
                      border-radius: 10px;
                      padding: 5px 10px;
                      font-size: 11px;
                      font-weight: 700;
                    }
                    QPushButton#retryButton:hover {
                      background: #efe3d2;
                    }
                    QPlainTextEdit#composerBox {
                      border: 1px solid #e1d3c2;
                      border-radius: 16px;
                      background: white;
                      padding: 10px;
                      min-height: 44px;
                    }
                    QLineEdit#resumeCommand {
                      border: 1px solid #e1d3c2;
                      border-radius: 12px;
                      background: #fffaf4;
                      padding: 7px 10px;
                      color: #4d3e31;
                      selection-background-color: #dfeee7;
                    }
                    QPushButton#copyButton {
                      background: #efe5d7;
                      color: #5b4a3c;
                      border: none;
                      border-radius: 12px;
                      padding: 7px 14px;
                      font-weight: 700;
                    }
                    QPushButton#copyButton:hover {
                      background: #e6dac8;
                    }
                    QComboBox#permissionSelect {
                      border: 1px solid #e1d3c2;
                      border-radius: 12px;
                      padding: 5px 10px;
                      min-width: 120px;
                      background: white;
                      color: #3b2f26;
                      font-size: 12px;
                      font-weight: 600;
                    }
                    QComboBox#permissionSelect:focus {
                      border: 1px solid #d28a4a;
                    }
                    QComboBox#permissionSelect::drop-down {
                      border: none;
                      width: 22px;
                    }
                    QPlainTextEdit#composerBox:focus {
                      border: 1px solid #d28a4a;
                    }
                    QPushButton#primaryButton {
                      background: #2f7b68;
                      color: white;
                      border: none;
                      border-radius: 16px;
                      min-width: 58px;
                      min-height: 34px;
                      padding: 6px 16px;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QPushButton#stopButton {
                      background: #fff5ee;
                      color: #a84834;
                      border: 1px solid #efc8b7;
                      border-radius: 16px;
                      min-width: 58px;
                      min-height: 34px;
                      padding: 6px 16px;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QPushButton#stopButton:hover {
                      background: #fde9dd;
                    }
                    QPushButton#ghostButton {
                      background: #efe5d7;
                      color: #5b4a3c;
                      border: none;
                      border-radius: 16px;
                      min-width: 72px;
                      min-height: 34px;
                      padding: 6px 14px;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QFrame#bubbleCardAssistant {
                      background: white;
                      border: 1px solid #eadfce;
                      border-radius: 18px;
                    }
                    QFrame#bubbleCardUser {
                      background: #f8efe4;
                      border: 1px solid #e4c9a5;
                      border-radius: 18px;
                    }
                    QLabel#bubbleHeader {
                      color: #8f7763;
                      font-size: 11px;
                      font-weight: 600;
                    }
                    QLabel#bubbleBody {
                      color: #2f241c;
                      font-size: 13px;
                      line-height: 1.6;
                    }
                    QLabel#statusText {
                      font-size: 14px;
                      font-weight: 700;
                      color: #2f7b68;
                    }
                    QLabel#statusText[tone="failure"] {
                      color: #c14f42;
                    }
                    QLabel#statusText[tone="success"] {
                      color: #2f7b68;
                    }
                    QScrollArea {
                      border: none;
                      background: transparent;
                    }
                    QScrollArea#chatScroll {
                      background: transparent;
                    }
                    QWidget#chatHost, QWidget#chatMessagesHost, QWidget#chatViewport {
                      background: transparent;
                    }
                    """
                )
