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
    def draft_session_summary(self) -> SessionSummary:
                return SessionSummary(
                    session_id="__new__",
                    thread_name="新会话",
                    updated_at="草稿",
                    updated_at_raw="",
                    cwd=str(self.new_session_work_dir),
                )

    def session_cwd(self, session: SessionSummary) -> str:
                override = (self.session_work_dir_overrides.get(session.session_id) or "").strip()
                return override or session.cwd or str(self.config.work_dir)

    def session_project(self, session: SessionSummary) -> CodexProject | None:
                explicit_id = self.session_project_ids.get(session.session_id)
                if explicit_id:
                    return self.project_by_id.get(explicit_id)

                try:
                    cwd = Path(self.session_cwd(session)).expanduser().resolve(strict=False)
                except (OSError, RuntimeError):
                    return None
                for project in self.projects:
                    for raw_root in project.root_paths:
                        try:
                            root = Path(raw_root).expanduser().resolve(strict=False)
                        except (OSError, RuntimeError):
                            continue
                        if cwd == root or root in cwd.parents:
                            return project
                return None

    def refresh_project_catalog(self) -> None:
                previous_collapsed = set(self.collapsed_project_ids)
                previous_ids = set(self.project_by_id)
                self.projects = load_codex_projects(self.config.codex_home)
                self.project_by_id = {project.project_id: project for project in self.projects}
                self.session_project_ids = {
                    session_id: project.project_id
                    for project in self.projects
                    for session_id in project.session_ids
                }
                self.collapsed_project_ids = {
                    project.project_id
                    for project in self.projects
                    if (
                        project.project_id in previous_collapsed
                        if project.project_id in previous_ids
                        else not project.expanded
                    )
                }

    def create_project(self) -> None:
                directory = QFileDialog.getExistingDirectory(
                    self,
                    "选择项目目录",
                    str(self.current_effective_work_dir()),
                )
                if not directory:
                    return
                root = Path(directory).expanduser().resolve()
                name, accepted = QInputDialog.getText(
                    self,
                    "新建项目",
                    "项目名称",
                    text=root.name,
                )
                if not accepted or not name.strip():
                    return
                try:
                    project_id = create_codex_local_project(
                        self.config.codex_home,
                        name.strip(),
                        root,
                    )
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    QMessageBox.critical(self, "Codex for Linux", f"创建项目失败：{exc}")
                    return

                self.refresh_project_catalog()
                self.selected_project_context_id = project_id
                self.collapsed_project_ids.discard(project_id)
                for project in self.projects:
                    project.selected = project.project_id == project_id
                self.session_scope = "all"
                self.apply_session_filters()
                self.set_status("项目已创建", "idle")

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
                    matched: list[SessionSummary] = []
                    for session in sessions:
                        project = self.session_project(session)
                        project_text = ""
                        if project:
                            project_text = " ".join([project.name, *project.root_paths]).lower()
                        if (
                            query in session.thread_name.lower()
                            or query in session.session_id.lower()
                            or query in self.session_cwd(session).lower()
                            or query in project_text
                        ):
                            matched.append(session)
                    sessions = matched
                self.filtered_sessions = sessions
                self.visible_session_limit = self.session_page_size
                self.refresh_session_list()

    def update_session_scope_buttons(self) -> None:
                return

    def update_session_list_selection(self) -> None:
                for row in range(self.session_list.count()):
                    item = self.session_list.item(row)
                    item_id = str(item.data(Qt.UserRole) or "")
                    widget = self.session_list.itemWidget(item)
                    if isinstance(widget, SessionListItem):
                        widget.set_selected(item_id == self.active_session_id)
                    elif isinstance(widget, ProjectGroupHeader):
                        project_id = item_id.removeprefix("__project__:")
                        widget.set_selected(project_id == self.selected_project_context_id)

    def refresh_session_list(self) -> None:
                filtered_sessions = self.filtered_sessions[:]
                query = self.search.text().strip()
                scroll_bar = self.session_list.verticalScrollBar()
                had_items = self.session_list.count() > 0
                scroll_position = scroll_bar.value()
                self.session_list.blockSignals(True)
                self.session_list.clear()
                current_row = -1

                show_draft = self.active_session_id is None

                def add_session_item(
                    session: SessionSummary,
                    selected: bool,
                    compact: bool = False,
                ) -> None:
                    nonlocal current_row
                    item = QListWidgetItem()
                    item.setData(Qt.UserRole, session.session_id)
                    preview_widget = SessionListItem(
                        session,
                        False,
                        query,
                        running=self.is_session_running(session.session_id),
                        unread=session.session_id in self.session_unread_ids,
                        compact=compact,
                    )
                    item.setSizeHint(preview_widget.sizeHint())
                    self.session_list.addItem(item)
                    self.session_list.setItemWidget(
                        item,
                        SessionListItem(
                            session,
                            selected,
                            query,
                            running=self.is_session_running(session.session_id),
                            unread=session.session_id in self.session_unread_ids,
                            compact=compact,
                        ),
                    )
                    if selected:
                        current_row = self.session_list.row(item)

                if show_draft:
                    add_session_item(self.draft_session_summary(), selected=True)

                pinned_sessions = [s for s in filtered_sessions if s.session_id in self.pinned_session_ids]
                regular_source = [s for s in filtered_sessions if s.session_id not in self.pinned_session_ids]

                def add_group(title: str, group_sessions: list[SessionSummary], time_grouped: bool) -> None:
                    nonlocal current_row
                    if not group_sessions:
                        return

                    def add_group_header(group: str, count: int) -> bool:
                        collapsed = group in self.collapsed_session_groups
                        header_item = QListWidgetItem()
                        header_item.setFlags(Qt.ItemIsEnabled)
                        header_item.setData(Qt.UserRole, f"__group__:{group}")
                        header_widget = SessionGroupHeader(
                            f"{group} {count}",
                            group_key=group,
                            collapsible=True,
                            collapsed=collapsed,
                        )
                        header_item.setSizeHint(header_widget.sizeHint())
                        self.session_list.addItem(header_item)
                        self.session_list.setItemWidget(header_item, header_widget)
                        return collapsed

                    if not time_grouped:
                        if add_group_header(title, len(group_sessions)):
                            return
                        visible_groups = [(title, group_sessions)]
                    else:
                        grouped_sessions: dict[str, list[SessionSummary]] = {}
                        for session in group_sessions:
                            group = session_group_label(session.updated_at_raw)
                            grouped_sessions.setdefault(group, []).append(session)
                        visible_groups = [
                            (group, grouped_sessions[group])
                            for group in ("今天", "昨天", "本周", "历史")
                            if group in grouped_sessions
                        ]

                    for group, sessions_in_group in visible_groups:
                        if time_grouped and add_group_header(group, len(sessions_in_group)):
                            continue
                        for session in sessions_in_group:
                            selected = session.session_id == self.active_session_id
                            add_session_item(session, selected)

                load_more_source: list[SessionSummary] = []
                displayed_sessions: list[SessionSummary] = []
                if self.session_scope == "all":
                    add_group("置顶", pinned_sessions, time_grouped=False)

                    if self.projects:
                        project_header = QListWidgetItem()
                        project_header.setFlags(Qt.NoItemFlags)
                        project_header_widget = SessionGroupHeader("项目", group_key="项目")
                        project_header.setSizeHint(project_header_widget.sizeHint())
                        self.session_list.addItem(project_header)
                        self.session_list.setItemWidget(project_header, project_header_widget)

                    sessions_by_project: dict[str, list[SessionSummary]] = {
                        project.project_id: [] for project in self.projects
                    }
                    projectless_sessions: list[SessionSummary] = []
                    for session in regular_source:
                        project = self.session_project(session)
                        if project:
                            sessions_by_project[project.project_id].append(session)
                        else:
                            projectless_sessions.append(session)

                    query_lower = query.lower()
                    for project in self.projects:
                        project_sessions = sessions_by_project.get(project.project_id, [])
                        project_text = " ".join([project.name, *project.root_paths]).lower()
                        if query_lower and query_lower not in project_text and not project_sessions:
                            continue

                        by_id = {session.session_id: session for session in project_sessions}
                        ordered_sessions = [
                            by_id.pop(session_id)
                            for session_id in project.session_ids
                            if session_id in by_id
                        ]
                        ordered_sessions.extend(by_id.values())
                        collapsed = project.project_id in self.collapsed_project_ids
                        header_item = QListWidgetItem()
                        header_item.setFlags(Qt.ItemIsEnabled)
                        header_item.setData(Qt.UserRole, f"__project__:{project.project_id}")
                        tooltip = "\n".join(project.root_paths)
                        header_widget = ProjectGroupHeader(
                            project.name,
                            tooltip,
                            collapsed,
                            project.selected,
                        )
                        header_item.setSizeHint(header_widget.sizeHint())
                        header_item.setToolTip(tooltip)
                        self.session_list.addItem(header_item)
                        self.session_list.setItemWidget(header_item, header_widget)
                        if not collapsed:
                            for session in ordered_sessions:
                                add_session_item(
                                    session,
                                    session.session_id == self.active_session_id,
                                    compact=True,
                                )

                    recent_sessions = projectless_sessions[: self.visible_session_limit]
                    if (
                        self.active_session_id
                        and self.active_session_id not in {s.session_id for s in recent_sessions}
                    ):
                        active_session = next(
                            (s for s in projectless_sessions if s.session_id == self.active_session_id),
                            None,
                        )
                        if active_session:
                            recent_sessions.append(active_session)
                    add_group("", recent_sessions, time_grouped=True)
                    load_more_source = projectless_sessions
                    displayed_sessions = recent_sessions
                elif self.session_scope == "pinned":
                    add_group("置顶", pinned_sessions, time_grouped=False)
                    displayed_sessions = pinned_sessions
                else:
                    regular_sessions = regular_source[: self.visible_session_limit]
                    add_group("置顶", pinned_sessions, time_grouped=False)
                    add_group("", regular_sessions, time_grouped=True)
                    load_more_source = regular_source
                    displayed_sessions = regular_sessions

                active_project_collapsed = False
                if self.session_scope == "all" and self.active_session_id:
                    active_summary = next(
                        (session for session in regular_source if session.session_id == self.active_session_id),
                        None,
                    )
                    active_project = self.session_project(active_summary) if active_summary else None
                    active_project_collapsed = bool(
                        active_project
                        and active_project.project_id in self.collapsed_project_ids
                    )

                if current_row >= 0:
                    self.session_list.setCurrentRow(current_row)
                elif not active_project_collapsed and not self.active_session_id:
                    for i in range(self.session_list.count()):
                        item = self.session_list.item(i)
                        item_id = item.data(Qt.UserRole) if item else ""
                        if item_id and not str(item_id).startswith("__project__:"):
                            self.session_list.setCurrentRow(i)
                            break
                self.session_list.blockSignals(False)
                self.load_more_button.setVisible(len(load_more_source) > len(displayed_sessions))
                if had_items:
                    def restore_scroll_position() -> None:
                        current_bar = self.session_list.verticalScrollBar()
                        current_bar.setValue(min(scroll_position, current_bar.maximum()))

                    restore_scroll_position()
                    QTimer.singleShot(0, restore_scroll_position)

    def refresh_sessions_for_account(self, keep_selection: bool = True) -> None:
                previous = self.active_session_id if keep_selection else None
                self.refresh_project_catalog()
                self.sessions = load_sessions(
                    self.config,
                    session_aliases=self.session_aliases,
                    force_refresh=True,
                )
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
                self.session_more_button.setEnabled(True)
                self.rename_session_action.setEnabled(has_session)
                self.copy_session_id_action.setEnabled(has_session)
                self.copy_resume_action.setEnabled(has_session)
                self.open_session_file_action.setEnabled(
                    has_session
                    and bool(find_session_file(self.config.codex_home, self.active_session_id))
                )
                self.edit_work_dir_action.setEnabled(True)
                self.clear_session_alias_action.setEnabled(
                    has_session and self.active_session_id in self.session_aliases
                )
                self.permission_combo.setEnabled(True)
                self.model_combo.setEnabled(True)
                self.reasoning_combo.setEnabled(True)

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
                if self.is_current_session_busy():
                    self.set_status("当前会话运行中，暂不能重命名", "idle")
                    return
                current_title = next(
                    (session.thread_name for session in self.sessions if session.session_id == self.active_session_id),
                    self.active_session_id[:8],
                )
                new_title, accepted = QInputDialog.getText(self, "重命名会话", "会话名称", text=current_title)
                if not accepted:
                    return
                alias = new_title.strip()
                if not alias:
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
