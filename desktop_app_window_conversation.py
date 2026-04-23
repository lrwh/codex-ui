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

class WindowConversationMixin:
    def refresh_attachment_widgets(self) -> None:
                self.clear_layout_widgets(self.attachment_list_layout)
                if not self.pending_attachments:
                    self.attachment_hint.setText("未添加")
                    self.attachment_hint.show()
                    return
                self.attachment_hint.hide()
                for index, item in enumerate(self.pending_attachments):
                    kind = "图" if item.kind == "image" else "文"
                    button = QPushButton(f"{kind} {truncate_text(attachment_label(item.path), 24)} ×")
                    button.setObjectName("scopeButton")
                    button.clicked.connect(lambda _checked=False, i=index: self.remove_attachment(i))
                    self.attachment_list_layout.addWidget(button, 0)
                self.attachment_list_layout.addStretch(1)

    def remove_attachment(self, index: int) -> None:
                if index < 0 or index >= len(self.pending_attachments):
                    return
                self.pending_attachments.pop(index)
                self.refresh_attachment_widgets()

    def pick_attachments(self) -> None:
                files, _ = QFileDialog.getOpenFileNames(
                    self,
                    "选择附件",
                    str(self.config.work_dir),
                    "支持的附件 (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.log *.md *.markdown);;"
                    "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;"
                    "日志 (*.log);;"
                    "Markdown (*.md *.markdown)",
                )
                if not files:
                    return
                unsupported: list[str] = []
                existing = {item.path for item in self.pending_attachments}
                for path in files:
                    kind = detect_attachment_kind(path)
                    if kind is None:
                        unsupported.append(Path(path).name)
                        continue
                    if path in existing:
                        continue
                    self.pending_attachments.append(AttachmentInfo(path=path, kind=kind))
                    existing.add(path)
                self.refresh_attachment_widgets()
                if unsupported:
                    QMessageBox.information(
                        self,
                        "codex-ui",
                        "以下附件类型暂不支持：\n" + "\n".join(unsupported),
                    )

    def current_request_key(self) -> str:
                return self.active_session_id or "__new__"

    def resolve_request_key(self, request_key: str) -> str:
                return self.worker_key_aliases.get(request_key, request_key)

    def has_running_worker(self) -> bool:
                return any(worker.isRunning() for worker in self.workers.values())

    def is_current_session_busy(self) -> bool:
                return self.current_request_key() in self.workers

    def update_request_controls(self) -> None:
                busy = self.is_current_session_busy()
                self.send_button.setEnabled(not busy)
                self.stop_button.setVisible(busy)
                self.stop_button.setEnabled(busy)
                self.add_attachment_button.setEnabled(not busy)
                self.input_box.setEnabled(not busy)
                self.new_button.setEnabled(True)
                if busy:
                    self.request_state_label.setObjectName("requestStateRunning")
                    self.request_state_label.setStyleSheet("")
                    self.request_state_label.setText("处理中...")
                    self.request_error_label.hide()
                    self.retry_button.hide()
                    return
                if self.request_state_label.text() in {"处理中...", "正在停止..."}:
                    self.set_request_ready_feedback()

    def begin_request_feedback(self, prompt: str, attachments: list[AttachmentInfo] | None = None) -> None:
                self.last_prompt = prompt
                self.last_attachments = [
                    AttachmentInfo(path=item.path, kind=item.kind) for item in (attachments or [])
                ]
                self.last_error = ""
                self.streaming_bubble = None
                self.streaming_text = ""
                self.request_state_label.setObjectName("requestStateRunning")
                self.request_state_label.setStyleSheet("")
                self.request_state_label.setText("处理中...")
                self.request_error_label.hide()
                self.request_error_label.setText("")
                self.retry_button.hide()
                self.update_request_controls()

    def finish_request_feedback(self) -> None:
                self.update_request_controls()
                if self.input_box.isEnabled():
                    self.input_box.setFocus()

    def set_request_ready_feedback(self) -> None:
                self.request_state_label.setObjectName("requestStateIdle")
                self.request_state_label.setStyleSheet("")
                self.request_state_label.setText("")
                self.request_error_label.hide()
                self.retry_button.hide()
                self.stop_button.hide()

    def set_request_failed_feedback(self, error: str) -> None:
                compact_error = " ".join((error or "未知错误").split()).strip()
                self.last_error = compact_error
                self.request_state_label.setObjectName("requestStateFailed")
                self.request_state_label.setStyleSheet("")
                self.request_state_label.setText("发送失败")
                self.request_error_label.setText(truncate_text(compact_error, 220))
                self.request_error_label.show()
                if self.last_prompt:
                    self.retry_button.show()
                self.input_box.setEnabled(True)
                self.send_button.setEnabled(True)
                self.add_attachment_button.setEnabled(True)

    def retry_last_prompt(self) -> None:
                if (not self.last_prompt and not self.last_attachments) or self.is_current_session_busy():
                    return
                self.input_box.setPlainText(self.last_prompt)
                self.pending_attachments = [AttachmentInfo(path=item.path, kind=item.kind) for item in self.last_attachments]
                self.refresh_attachment_widgets()
                self.send_prompt()

    def stop_current_request(self) -> None:
                request_key = self.current_request_key()
                worker = self.workers.get(request_key)
                if worker is None or not worker.isRunning():
                    return
                self.request_state_label.setObjectName("requestStateRunning")
                self.request_state_label.setStyleSheet("")
                self.request_state_label.setText("正在停止...")
                self.stop_button.setEnabled(False)
                self.set_status("正在停止当前请求...", "running")
                self.restore_request_account()
                worker.stop()

    def on_session_selected(self, row: int) -> None:
                if row < 0:
                    return
                item = self.session_list.item(row)
                if not item:
                    return
                session_id = item.data(Qt.UserRole)
                if not session_id:
                    return
                self.active_session_id = session_id
                self.refresh_session_list()
                self.load_active_session(scroll_to_top=False)

    def current_session_messages(self) -> list[ChatMessage]:
                if not self.active_session_id:
                    return []
                cached = self.session_message_cache.get(self.active_session_id)
                if cached:
                    cached_path = Path(cached.session_path)
                    if cached_path.is_file():
                        try:
                            mtime_ns = cached_path.stat().st_mtime_ns
                        except OSError:
                            mtime_ns = -1
                        if mtime_ns == cached.mtime_ns:
                            return cached.messages
                path, mtime_ns = conversation_file_info(self.config.codex_home, self.active_session_id)
                if not path:
                    self.session_message_cache.pop(self.active_session_id, None)
                    return []
                messages = load_conversation_from_path(path)
                self.session_message_cache[self.active_session_id] = ConversationCacheEntry(
                    session_path=str(path),
                    mtime_ns=mtime_ns,
                    messages=messages,
                )
                return messages

    def update_message_load_controls(self) -> None:
                total = len(self.active_session_messages)
                visible = total - self.active_message_start_index if total else 0
                if total <= 0:
                    self.message_count_label.setText("暂无消息")
                    self.load_more_messages_button.hide()
                    return
                if self.active_message_start_index > 0:
                    hidden = self.active_message_start_index
                    self.message_count_label.setText(f"已显示最近 {visible} / {total} 条")
                    self.load_more_messages_button.setText(f"加载更早消息 ({hidden})")
                    self.load_more_messages_button.show()
                    return
                self.message_count_label.setText(f"共 {total} 条消息")
                self.load_more_messages_button.hide()

    def clear_messages(self) -> None:
                self.streaming_bubbles.pop(self.current_request_key(), None)
                self.active_session_messages = []
                self.active_message_start_index = 0
                self.streaming_bubble = None
                self.streaming_text = ""
                while self.chat_layout.count() > 0:
                    item = self.chat_layout.takeAt(0)
                    widget = item.widget()
                    if widget:
                        widget.deleteLater()
                self.update_message_load_controls()

    def add_message(
                self,
                message: ChatMessage,
                auto_scroll: bool = True,
                insert_index: int | None = None,
            ) -> MessageBubble:
                bubble = MessageBubble(message)
                if insert_index is None:
                    self.chat_layout.addWidget(bubble)
                else:
                    self.chat_layout.insertWidget(insert_index, bubble)
                if auto_scroll:
                    self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum())
                return bubble

    def load_older_messages(self) -> None:
                if self.active_message_start_index <= 0 or not self.active_session_messages:
                    return
                scrollbar = self.chat_scroll.verticalScrollBar()
                old_value = scrollbar.value()
                old_maximum = scrollbar.maximum()
                new_start = max(0, self.active_message_start_index - self.message_render_chunk_size)
                older_messages = self.active_session_messages[new_start : self.active_message_start_index]
                insert_index = 0
                for message in older_messages:
                    self.add_message(message, auto_scroll=False, insert_index=insert_index)
                    insert_index += 1
                self.active_message_start_index = new_start
                self.update_message_load_controls()

                def restore_position() -> None:
                    current = self.chat_scroll.verticalScrollBar()
                    current.setValue(old_value + max(0, current.maximum() - old_maximum))

                QTimer.singleShot(0, restore_position)
                QTimer.singleShot(30, restore_position)

    def append_assistant_delta(self, request_key: str, delta: str) -> None:
                request_key = self.resolve_request_key(request_key)
                chunk = delta or ""
                if not chunk:
                    return
                current_text = self.streaming_texts.get(request_key, "")
                current_text += chunk
                self.streaming_texts[request_key] = current_text
                if request_key != self.current_request_key():
                    return
                bubble = self.streaming_bubbles.get(request_key)
                if bubble is None:
                    bubble = self.add_message(
                        ChatMessage(role="assistant", text="", timestamp=current_local_time()),
                        auto_scroll=False,
                    )
                    self.streaming_bubbles[request_key] = bubble
                    self.streaming_bubble = bubble
                bubble.update_text(current_text)
                self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum())

    def finalize_assistant_message(self, request_key: str, text: str) -> None:
                request_key = self.resolve_request_key(request_key)
                final_text = text.strip()
                if not final_text:
                    return
                self.streaming_texts[request_key] = final_text
                bubble = self.streaming_bubbles.get(request_key)
                if bubble is not None:
                    bubble.update_text(final_text)
                    self.streaming_bubbles.pop(request_key, None)
                    if self.streaming_bubble is bubble:
                        self.streaming_bubble = None
                    return
                if request_key != self.current_request_key():
                    return
                self.add_message(ChatMessage(role="assistant", text=final_text, timestamp=current_local_time()))

    def load_active_session(self, scroll_to_top: bool = False) -> None:
                self.clear_messages()
                if not self.is_current_session_busy():
                    self.set_request_ready_feedback()
                if not self.active_session_id:
                    if self.sessions:
                        self.header_title.setText("新会话")
                        self.header_meta.setText("尚未开始")
                    else:
                        self.header_title.setText("暂无会话")
                        self.header_meta.setText("当前本地还没有会话")
                    self.resume_command.setText("")
                    self.set_status("")
                    self.update_pin_button()
                    self.update_session_action_buttons()
                    self.message_count_label.setText("暂无消息")
                    self.update_request_controls()
                    return

                session = next((s for s in self.sessions if s.session_id == self.active_session_id), None)
                if not session:
                    session = load_session_summary(self.config, self.active_session_id, self.session_aliases)
                self.header_title.setText(session.thread_name if session else self.active_session_id[:8])
                self.header_meta.setText(f"{session.updated_at if session else '-'} · {self.active_session_id[:8]}")
                self.resume_command.setText(f"codex resume {self.active_session_id}")
                self.update_pin_button()
                self.update_session_action_buttons()
                messages = self.current_session_messages()
                self.active_session_messages = messages
                total = len(messages)
                self.active_message_start_index = max(0, total - self.initial_message_render_limit)
                visible_messages = messages[self.active_message_start_index :]
                for msg in visible_messages:
                    self.add_message(msg, auto_scroll=False)
                self.update_message_load_controls()
                streaming_text = self.streaming_texts.get(self.current_request_key(), "")
                if streaming_text:
                    bubble = self.add_message(
                        ChatMessage(role="assistant", text=streaming_text, timestamp=current_local_time()),
                        auto_scroll=False,
                    )
                    self.streaming_bubbles[self.current_request_key()] = bubble
                if visible_messages:
                    self.reset_chat_scroll(to_top=scroll_to_top)
                self.update_request_controls()

    def reset_chat_scroll(self, to_top: bool) -> None:
                target = 0 if to_top else self.chat_scroll.verticalScrollBar().maximum()

                def apply_position() -> None:
                    scrollbar = self.chat_scroll.verticalScrollBar()
                    scrollbar.setValue(0 if to_top else scrollbar.maximum())

                scrollbar = self.chat_scroll.verticalScrollBar()
                scrollbar.setValue(target)
                QTimer.singleShot(0, apply_position)
                QTimer.singleShot(30, apply_position)

    def copy_resume_command(self) -> None:
                command = self.resume_command.text().strip()
                if not command:
                    return
                QGuiApplication.clipboard().setText(command)
                self.set_status("已复制 resume 命令", "idle")

    def insert_prompt_template(self, template: str) -> None:
                current = self.input_box.toPlainText()
                if not current.strip():
                    self.input_box.setPlainText(template)
                else:
                    self.input_box.setPlainText(f"{current.rstrip()}\n\n{template}")
                self.input_box.setFocus()
                self.input_box.moveCursor(QTextCursor.End)

    def normalize_model_command_value(self, raw: str) -> str:
                value = (raw or "").strip()
                if value.lower() in {"default", "reset", "none"} or value in {"默认", "清空"}:
                    return ""
                return value

    def model_settings_label(self, model: str, reasoning_effort: str) -> str:
                return (
                    f"模型: {model_display_name(model)}"
                    f" · 推理: {reasoning_effort_display_name(reasoning_effort)}"
                )

    def apply_model_settings(
                self,
                model: str | None = None,
                reasoning_effort: str | None = None,
            ) -> None:
                next_model = self.config.model if model is None else self.normalize_model_command_value(model)
                next_reasoning_effort = (
                    self.config.model_reasoning_effort
                    if reasoning_effort is None
                    else normalize_reasoning_effort(reasoning_effort)
                )
                if next_model == self.config.model and next_reasoning_effort == self.config.model_reasoning_effort:
                    self.set_status(f"当前{self.model_settings_label(next_model, next_reasoning_effort)}", "idle")
                    return
                self.config.model = next_model
                self.config.model_reasoning_effort = next_reasoning_effort
                save_config(self.config)
                self.set_status(f"已切换 {self.model_settings_label(next_model, next_reasoning_effort)}", "idle")

    def open_model_selection_dialog(self) -> None:
                dialog = ModelSelectionDialog(self)
                if dialog.exec() != QDialog.Accepted:
                    self.set_status("已取消模型选择", "idle")
                    return
                self.apply_model_settings(dialog.selected_model, dialog.selected_reasoning_effort)

    def parse_model_command_args(self, raw: str) -> tuple[str | None, str | None]:
                tokens = (raw or "").split()
                if not tokens:
                    return None, None
                if len(tokens) == 1 and normalize_reasoning_effort(tokens[0]):
                    return None, tokens[0]
                if len(tokens) == 1 and tokens[0] in {"默认", "清空"}:
                    return "", None
                model = tokens[0]
                reasoning_effort = tokens[1] if len(tokens) > 1 else None
                return model, reasoning_effort

    def handle_composer_command(self, prompt: str) -> bool:
                stripped = (prompt or "").strip()
                if not stripped.startswith("/"):
                    return False
                parts = stripped.split(None, 1)
                command = parts[0] if parts else ""
                rest = parts[1] if len(parts) > 1 else ""
                if command.strip().lower() != "/model":
                    return False
                self.input_box.clear()
                if rest.strip():
                    model, reasoning_effort = self.parse_model_command_args(rest)
                    self.apply_model_settings(model, reasoning_effort)
                else:
                    self.open_model_selection_dialog()
                if self.input_box.isEnabled():
                    self.input_box.setFocus()
                return True

    def new_session(self) -> None:
                self.active_session_id = None
                self.refresh_session_list()
                self.load_active_session(scroll_to_top=True)
                self.set_status("已切换到新会话", "idle")

    def send_prompt(self) -> None:
                request_key = self.current_request_key()
                if request_key in self.workers:
                    return
                prompt = self.input_box.toPlainText().strip()
                attachments = [AttachmentInfo(path=item.path, kind=item.kind) for item in self.pending_attachments]
                if not prompt and not attachments:
                    return
                if prompt and self.handle_composer_command(prompt):
                    return
                missing_paths = [item.path for item in attachments if not Path(item.path).exists()]
                if missing_paths:
                    QMessageBox.critical(
                        self,
                        "codex-ui",
                        "以下附件不存在，无法发送：\n" + "\n".join(missing_paths),
                    )
                    return
                try:
                    final_prompt = build_prompt_with_attachments(prompt, attachments)
                except OSError as exc:
                    QMessageBox.critical(self, "codex-ui", str(exc))
                    return
                if not final_prompt:
                    QMessageBox.critical(self, "codex-ui", "附件内容为空，无法发送。")
                    return
                visible_prompt = prompt or ""
                attachment_summary = render_attachment_summary(attachments)
                if attachment_summary:
                    visible_prompt = f"{visible_prompt}\n\n{attachment_summary}".strip()
                self.add_message(ChatMessage(role="user", text=visible_prompt, timestamp=current_local_time()))
                self.mark_session_updated(self.active_session_id)
                self.remember_request_account()
                self.input_box.clear()
                self.pending_attachments = []
                self.refresh_attachment_widgets()
                self.set_status("", "running")

                image_paths = [item.path for item in attachments if item.kind == "image"]
                worker = CodexWorker(self.config, self.active_session_id, final_prompt, image_paths=image_paths)
                self.workers[request_key] = worker
                self.worker = worker
                self.begin_request_feedback(prompt, attachments)
                worker.session_started.connect(lambda session_id, key=request_key: self.on_session_started(key, session_id))
                worker.assistant_delta.connect(lambda text, key=request_key: self.on_assistant_delta(key, text))
                worker.assistant_message.connect(lambda text, key=request_key: self.on_assistant_message(key, text))
                worker.usage_updated.connect(lambda usage, key=request_key: self.on_usage_updated(key, usage))
                worker.failed.connect(lambda error, key=request_key: self.on_failed(key, error))
                worker.finished_ok.connect(lambda key=request_key: self.on_finished_ok(key))
                worker.finished.connect(self.on_worker_thread_finished)
                worker.start()

    def on_session_started(self, request_key: str, session_id: str) -> None:
                if session_id:
                    self.worker_key_aliases[request_key] = session_id
                    worker = self.workers.pop(request_key, None)
                    if worker is not None:
                        self.workers[session_id] = worker
                    if request_key in self.streaming_texts:
                        self.streaming_texts[session_id] = self.streaming_texts.pop(request_key)
                    if request_key in self.streaming_bubbles:
                        self.streaming_bubbles[session_id] = self.streaming_bubbles.pop(request_key)
                    self.active_session_id = session_id
                    self.bind_session_to_active_account(session_id)
                    self.mark_session_updated(session_id)
                    self.update_request_controls()

    def on_assistant_message(self, request_key: str, text: str) -> None:
                request_key = self.resolve_request_key(request_key)
                self.finalize_assistant_message(request_key, text)
                self.mark_session_updated(request_key if request_key != "__new__" else self.active_session_id)

    def on_assistant_delta(self, request_key: str, text: str) -> None:
                self.append_assistant_delta(request_key, text)

    def on_usage_updated(self, request_key: str, usage: dict) -> None:
                request_key = self.resolve_request_key(request_key)
                if request_key != self.current_request_key():
                    return
                self.usage_label.setText(
                    "in "
                    f"{humanize_count(usage.get('input_tokens', 0))}"
                    " · cache "
                    f"{humanize_count(usage.get('cached_input_tokens', 0))}"
                    " · out "
                    f"{humanize_count(usage.get('output_tokens', 0))}"
                )

    def on_failed(self, request_key: str, error: str) -> None:
                request_key = self.resolve_request_key(request_key)
                self.streaming_bubbles.pop(request_key, None)
                if request_key == self.current_request_key():
                    self.streaming_bubble = None
                self.restore_request_account()
                if request_key == self.current_request_key():
                    self.finish_request_feedback()
                    self.set_request_failed_feedback(error)
                self.set_status("发送失败", "idle")

    def on_finished_ok(self, request_key: str) -> None:
                request_key = self.resolve_request_key(request_key)
                self.streaming_bubbles.pop(request_key, None)
                self.streaming_texts.pop(request_key, None)
                self.restore_request_account()
                if request_key == self.current_request_key():
                    self.finish_request_feedback()
                    self.set_request_ready_feedback()
                    self.load_active_session(scroll_to_top=False)
                self.bind_session_to_active_account(request_key if request_key != "__new__" else self.active_session_id)
                self.set_status("", "idle")

    def on_worker_thread_finished(self) -> None:
                worker = self.sender()
                was_stopping = self.request_state_label.text() == "正在停止..."
                for key, running_worker in list(self.workers.items()):
                    if running_worker is worker:
                        self.workers.pop(key, None)
                        for alias, resolved in list(self.worker_key_aliases.items()):
                            if resolved == key:
                                self.worker_key_aliases.pop(alias, None)
                        break
                if worker is self.worker:
                    self.worker = None
                worker.deleteLater()
                self.update_request_controls()
                if was_stopping:
                    self.set_status("已停止当前请求", "idle")

    def closeEvent(self, event: QCloseEvent) -> None:
                for worker in list(self.workers.values()):
                    if worker.isRunning():
                        worker.stop()
                        worker.wait(3000)
                if self.account_worker is not None and self.account_worker.isRunning():
                    self.account_worker.stop()
                    self.account_worker.wait(3000)
                event.accept()
