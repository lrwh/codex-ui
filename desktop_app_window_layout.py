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
    QMenu,
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
                self.version_label = QLabel(f"v{self.app_version}")
                self.version_label.setObjectName("sidebarMeta")
                brand_stack.addWidget(title)
                brand_stack.addWidget(self.version_label)
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
                section_row = QHBoxLayout()
                section_row.setContentsMargins(0, 0, 0, 0)
                section_row.addWidget(section_label, 0)
                section_row.addStretch(1)
                self.create_project_button = QPushButton("新建项目")
                self.create_project_button.setObjectName("scopeButton")
                self.create_project_button.clicked.connect(self.create_project)
                section_row.addWidget(self.create_project_button, 0)

                self.search = QLineEdit()
                self.search.setObjectName("searchInput")
                self.search.setPlaceholderText("搜索项目、标题或 ID")
                self.search.textChanged.connect(self.on_search)
                self.session_list = QListWidget()
                self.session_list.setObjectName("sessionList")
                self.session_list.itemClicked.connect(self.on_session_item_clicked)
                self.session_list.itemActivated.connect(self.on_session_item_clicked)
                self.session_list.setFrameShape(QFrame.NoFrame)
                self.session_list.setSpacing(1)
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
                layout.addLayout(section_row)
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
                self.pin_button = QPushButton("置顶")
                self.pin_button.setObjectName("pinButton")
                self.pin_button.clicked.connect(self.toggle_pin_active_session)
                self.session_more_button = QPushButton("更多")
                self.session_more_button.setObjectName("moreButton")
                self.session_more_menu = QMenu(self.session_more_button)
                self.rename_session_action = self.session_more_menu.addAction("重命名", self.rename_current_session)
                self.copy_session_id_action = self.session_more_menu.addAction("复制会话 ID", self.copy_current_session_id)
                self.copy_resume_action = self.session_more_menu.addAction("复制恢复命令", self.copy_resume_command)
                self.open_session_file_action = self.session_more_menu.addAction("打开会话文件", self.open_current_session_file)
                self.edit_work_dir_action = self.session_more_menu.addAction("修改工作目录", self.edit_current_work_dir)
                self.clear_session_alias_action = self.session_more_menu.addAction("清除本地别名", self.clear_current_session_alias)
                self.session_more_button.setMenu(self.session_more_menu)
                header_row = QHBoxLayout()
                header_row.setContentsMargins(0, 0, 0, 0)
                header_row.setSpacing(10)
                header_row.addWidget(self.work_dir_label, 0)
                header_row.addStretch(1)
                self.header_title_row.insertWidget(1, self.header_title, 0)
                self.header_title_row.addWidget(self.pin_button, 0)
                self.header_title_row.addWidget(self.session_more_button, 0)
                self.header_title_row.addWidget(self.header_meta, 0)
                self.header_title_row.addWidget(self.header_status, 0, Qt.AlignRight)
                self.header_card.layout().addLayout(header_row)
                self.resume_command = QLineEdit(self.header_card)
                self.resume_command.setObjectName("resumeCommand")
                self.resume_command.setReadOnly(True)
                self.resume_command.setFocusPolicy(Qt.ClickFocus)
                self.resume_command.hide()
                self.permission_combo = QComboBox()
                self.permission_combo.setObjectName("permissionSelect")
                self.permission_combo.addItem("工作区", "workspace")
                self.permission_combo.addItem("只读", "readonly")
                self.permission_combo.addItem("全权限", "full")
                self.permission_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                self.permission_combo.setMinimumContentsLength(6)
                self.permission_combo.setFixedWidth(112)
                self.permission_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                self.permission_combo.currentIndexChanged.connect(self.on_permission_preset_changed)
                self.model_combo = QComboBox()
                self.model_combo.setObjectName("modelSelect")
                self.model_combo.setFixedWidth(182)
                self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                self.model_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                for model in model_choices(self.config.model, self.config.codex_home):
                    self.model_combo.addItem("CLI 默认" if not model else model, model)
                self.model_combo.currentIndexChanged.connect(self.on_model_selector_changed)
                self.reasoning_combo = QComboBox()
                self.reasoning_combo.setObjectName("modelSelect")
                self.reasoning_combo.setFixedWidth(96)
                self.reasoning_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                for label, value in DEFAULT_REASONING_EFFORT_CHOICES:
                    self.reasoning_combo.addItem(label, value)
                self.reasoning_combo.currentIndexChanged.connect(self.on_model_selector_changed)
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
                self.input_card.setMinimumHeight(250)
                self.input_card.setMaximumHeight(330)
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
                self.input_box.setFixedHeight(136)
                self.input_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
                self.input_box.command_requested.connect(self.send_prompt)
                self.input_box.attachments_pasted.connect(self.add_pasted_attachments)
                self.input_box.clipboard_image_pasted.connect(self.add_clipboard_image_attachment)
                template_row = QHBoxLayout()
                template_row.setContentsMargins(0, 0, 0, 0)
                template_row.setSpacing(8)
                self.add_attachment_button = self.make_scope_button("添加附件", self.pick_attachments)
                template_row.addWidget(self.add_attachment_button, 0)
                self.prompt_menu_button = QPushButton("快捷指令")
                self.prompt_menu_button.setObjectName("scopeButton")
                self.prompt_menu = QMenu(self.prompt_menu_button)
                for label, template in self.prompt_templates:
                    action = self.prompt_menu.addAction(label)
                    action.triggered.connect(
                        lambda _checked=False, content=template: self.insert_prompt_template(content)
                    )
                self.prompt_menu_button.setMenu(self.prompt_menu)
                template_row.addWidget(self.prompt_menu_button, 0)
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
                model_label = QLabel("模型")
                model_label.setObjectName("cardMeta")
                reasoning_label = QLabel("推理")
                reasoning_label.setObjectName("cardMeta")
                button_row = QHBoxLayout()
                button_row.setContentsMargins(0, 0, 0, 0)
                button_row.setSpacing(8)
                button_row.addWidget(model_label, 0)
                button_row.addWidget(self.model_combo, 0)
                button_row.addWidget(reasoning_label, 0)
                button_row.addWidget(self.reasoning_combo, 0)
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
                status_layout.setContentsMargins(8, 6, 8, 4)
                status_layout.setSpacing(14)
                self.usage_label = QLabel("in 0 · cache 0 · out 0")
                self.usage_label.setObjectName("cardMeta")
                self.help_label = QLabel("搜索 /  ·  Ctrl+Enter 发送  ·  Ctrl+N 新会话")
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
                    layout.setContentsMargins(8, 6, 8, 8)
                    layout.setSpacing(3)
                elif object_name == "inputCard":
                    layout.setContentsMargins(8, 8, 8, 6)
                    layout.setSpacing(6)
                else:
                    layout.setContentsMargins(8, 8, 8, 8)
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
                      background: #fbfaf7;
                      color: #202923;
                    }
                    QDialog#accountDialog {
                      background: #fbfaf7;
                      color: #202923;
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
                      color: #1f2823;
                    }
                    QLabel#pageSubtitle {
                      color: #68736d;
                      font-size: 14px;
                    }
                    QFrame#sidebar {
                      background: #f7f8f5;
                      border-right: 1px solid #e4ddd3;
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
                      color: #1f2823;
                    }
                    QLabel#sidebarMeta {
                      color: #7b6f65;
                      font-size: 12px;
                      font-weight: 600;
                    }
                    QLabel#sidebarPath {
                      color: #968678;
                      font-size: 11px;
                    }
                    QLabel#sidebarAccount {
                      color: #2f7b68;
                      font-size: 11px;
                      font-weight: 600;
                    }
                    QLabel#sidebarHint {
                      color: #827469;
                      font-size: 11px;
                    }
                    QPushButton#scopeButton {
                      background: transparent;
                      color: #52605a;
                      border: 1px solid #dfe5e1;
                      border-radius: 9px;
                      padding: 4px 9px;
                      font-size: 11px;
                      font-weight: 600;
                    }
                    QPushButton#scopeButton:hover {
                      background: #edf4f0;
                      color: #276b5a;
                    }
                    QPushButton#scopeButton[selected="true"] {
                      background: #2f7b68;
                      color: white;
                      border-color: #2f7b68;
                    }
                    QLabel#sidebarSection {
                      color: #bd642d;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QFrame#accountCard {
                      background: #ffffff;
                      border: 1px solid #dde3df;
                      border-radius: 12px;
                    }
                    QFrame#accountCard[active="true"] {
                      background: #e9f0ec;
                      border-color: #cbdad2;
                    }
                    QLabel#accountTitle {
                      color: #253029;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QLabel#accountMeta {
                      color: #758079;
                      font-size: 10px;
                    }
                    QLabel#accountUsage {
                      color: #59655e;
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
                      background: #e5ece8;
                      color: #466258;
                      border: none;
                      border-radius: 10px;
                      padding: 4px 10px;
                      font-size: 11px;
                      font-weight: 700;
                      min-width: 44px;
                    }
                    QLineEdit#searchInput {
                      border: 1px solid #ddd4c8;
                      border-radius: 12px;
                      padding: 7px 10px;
                      background: white;
                      color: #29332e;
                    }
                    QLineEdit#searchInput:focus {
                      border: 1px solid #b77a4c;
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
                      background: #d7c7b5;
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
                      border-radius: 8px;
                    }
                    QFrame#sessionGroupHeader[collapsible="true"]:hover {
                      background: #eaf0ed;
                    }
                    QLabel#sessionGroupCaret {
                      color: #69756e;
                      font-size: 12px;
                    }
                    QLabel#sessionGroupIcon {
                      color: #5e756b;
                      font-size: 10px;
                      min-width: 13px;
                      max-width: 13px;
                    }
                    QFrame#projectGroupHeader {
                      background: transparent;
                      border: none;
                      border-radius: 10px;
                    }
                    QFrame#projectGroupHeader[selected="true"] {
                      background: #e4ece8;
                    }
                    QFrame#projectGroupHeader:hover {
                      background: #eaf0ed;
                    }
                    QLabel#projectCaret {
                      color: #69756e;
                      font-size: 12px;
                    }
                    QLabel#projectGroupTitle {
                      color: #344139;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QLabel#sessionGroupTitle {
                      color: #8a7561;
                      font-size: 11px;
                      font-weight: 700;
                      letter-spacing: 1px;
                    }
                    QFrame#sessionCard[selected="true"] {
                      background: #e7f0eb;
                      border-left: 3px solid #2f7b68;
                    }
                    QFrame#sessionCard:hover {
                      background: #eef3f0;
                    }
                    QLabel#sessionTitle {
                      font-size: 12px;
                      font-weight: 700;
                      color: #26312b;
                    }
                    QLabel#sessionMeta {
                      font-size: 10px;
                      color: #758079;
                    }
                    QLabel#sessionDot {
                      color: #b9c2bd;
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
                    QFrame#headerCard {
                      background: transparent;
                      border: none;
                      border-bottom: 1px solid #e7ded2;
                      border-radius: 0px;
                    }
                    QFrame#conversationPanel {
                      background: transparent;
                      border: none;
                    }
                    QFrame#statusBar {
                      background: transparent;
                      border: none;
                      border-top: 1px solid #e7ded2;
                      border-radius: 0px;
                    }
                    QFrame#chatCard, QFrame#inputCard {
                      background: transparent;
                      border: none;
                    }
                    QFrame#conversationDivider {
                      background: #e7ded2;
                      border: none;
                      margin: 0 8px 0 8px;
                    }
                    QLabel#cardTitle {
                      font-size: 12px;
                      font-weight: 700;
                      color: #bd642d;
                    }
                    QLabel#cardHeadline {
                      font-size: 16px;
                      font-weight: 700;
                      color: #202923;
                    }
                    QLabel#cardMeta {
                      color: #7f6f62;
                      font-size: 12px;
                    }
                    QLabel#cardMetaStrong {
                      color: #5c4d40;
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
                      background: transparent;
                      color: #5f665f;
                      border: 1px solid #dfe3df;
                      border-radius: 10px;
                      padding: 5px 10px;
                      font-weight: 600;
                    }
                    QPushButton#pinButton:hover {
                      background: #f0f3f1;
                    }
                    QPushButton#pinButton:disabled {
                      color: #9ba59f;
                      background: #f0f3f1;
                    }
                    QPushButton#moreButton {
                      background: transparent;
                      color: #5f665f;
                      border: 1px solid #dfe3df;
                      border-radius: 10px;
                      padding: 5px 10px;
                      font-size: 11px;
                      font-weight: 600;
                    }
                    QPushButton#moreButton:hover {
                      background: #f0f3f1;
                    }
                    QMenu {
                      background: #ffffff;
                      color: #303630;
                      border: 1px solid #dde2dd;
                      border-radius: 10px;
                      padding: 6px;
                    }
                    QMenu::item {
                      border-radius: 7px;
                      padding: 7px 20px 7px 10px;
                    }
                    QMenu::item:selected {
                      background: #eaf2ee;
                      color: #245f51;
                    }
                    QMenu::item:disabled {
                      color: #a7aaa7;
                    }
                    QLabel#requestStateIdle {
                      color: #7f6f62;
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
                      background: #e8eeea;
                      color: #405149;
                      border: 1px solid #d9e0dc;
                      border-radius: 10px;
                      padding: 5px 10px;
                      font-size: 11px;
                      font-weight: 700;
                    }
                    QPushButton#retryButton:hover {
                      background: #dfe8e3;
                    }
                    QPlainTextEdit#composerBox {
                      border: 1px solid #ddd4c8;
                      border-radius: 16px;
                      background: white;
                      padding: 10px;
                      min-height: 44px;
                    }
                    QLineEdit#resumeCommand {
                      border: 1px solid #ddd4c8;
                      border-radius: 12px;
                      background: #ffffff;
                      padding: 7px 10px;
                      color: #344139;
                      selection-background-color: #dfeee7;
                    }
                    QPushButton#copyButton {
                      background: #f1e8dc;
                      color: #5c4d40;
                      border: none;
                      border-radius: 12px;
                      padding: 7px 14px;
                      font-weight: 700;
                    }
                    QPushButton#copyButton:hover {
                      background: #e9ddcd;
                    }
                    QComboBox#permissionSelect {
                      border: 1px solid #ddd4c8;
                      border-radius: 12px;
                      padding: 5px 10px;
                      min-width: 120px;
                      background: white;
                      color: #29332e;
                      font-size: 12px;
                      font-weight: 600;
                    }
                    QComboBox#permissionSelect:focus {
                      border: 1px solid #b77a4c;
                    }
                    QComboBox#permissionSelect::drop-down {
                      border: none;
                      width: 22px;
                    }
                    QComboBox#modelSelect {
                      border: 1px solid #ddd4c8;
                      border-radius: 12px;
                      padding: 5px 10px;
                      background: white;
                      color: #29332e;
                      font-size: 12px;
                      font-weight: 600;
                    }
                    QComboBox#modelSelect:focus {
                      border: 1px solid #b77a4c;
                    }
                    QComboBox#modelSelect::drop-down {
                      border: none;
                      width: 22px;
                    }
                    QPlainTextEdit#composerBox:focus {
                      border: 1px solid #b77a4c;
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
                      background: #f1e8dc;
                      color: #5c4d40;
                      border: none;
                      border-radius: 16px;
                      min-width: 72px;
                      min-height: 34px;
                      padding: 6px 14px;
                      font-size: 12px;
                      font-weight: 700;
                    }
                    QFrame#bubbleCardAssistant {
                      background: #ffffff;
                      border: 1px solid #e7ddd0;
                      border-radius: 18px;
                    }
                    QFrame#bubbleCardUser {
                      background: #f8efe4;
                      border: 1px solid #e8cfb0;
                      border-radius: 18px;
                    }
                    QLabel#bubbleHeader {
                      color: #8a7561;
                      font-size: 11px;
                      font-weight: 600;
                    }
                    QLabel#bubbleBody {
                      color: #202923;
                      font-size: 12px;
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
