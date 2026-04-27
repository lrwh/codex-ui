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

class WindowCommonMixin:
    def update_version_label(self) -> None:
                if not hasattr(self, "version_label"):
                    return
                text = f"v{self.app_version}"
                tooltip = f"当前版本：v{self.app_version}"
                if self.latest_release and is_newer_version(self.latest_release.version, self.app_version):
                    text += " · 有更新"
                    tooltip += f"\n最新版本：v{self.latest_release.version}"
                self.version_label.setText(text)
                self.version_label.setToolTip(tooltip)

    def start_background_release_check(self) -> None:
                if self.update_check_worker is not None:
                    return
                self.update_check_worker = ReleaseCheckWorker(APP_RELEASE_REPO)
                self.update_check_worker.finished_ok.connect(self.on_background_release_check_finished)
                self.update_check_worker.failed.connect(self.on_background_release_check_failed)
                self.update_check_worker.finished.connect(self.on_background_release_check_thread_finished)
                self.update_check_worker.start()

    def on_background_release_check_finished(self, release: ReleaseInfo) -> None:
                if is_newer_version(release.version, self.app_version):
                    self.latest_release = release
                else:
                    self.latest_release = None
                self.update_version_label()

    def on_background_release_check_failed(self, _error: str) -> None:
                self.update_version_label()

    def on_background_release_check_thread_finished(self) -> None:
                if self.update_check_worker is not None:
                    self.update_check_worker.deleteLater()
                    self.update_check_worker = None

    def run_commit_workflow(self) -> tuple[bool, str]:
                work_dir = self.current_effective_work_dir()
                repo = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=work_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if repo.returncode != 0:
                    return False, "当前工作目录不在 git 仓库内，无法提交代码。"
                repo_root = Path((repo.stdout or "").strip())
                if not repo_root:
                    return False, "未能识别 git 仓库根目录。"

                status = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                status_lines = [line.rstrip() for line in (status.stdout or "").splitlines() if line.strip()]
                if status.returncode != 0:
                    return False, (status.stderr or status.stdout or "读取 git 状态失败。").strip()
                if not status_lines:
                    return False, "当前没有可提交的改动。"

                self.update_daily_changelog(repo_root, status_lines)

                add_result = subprocess.run(
                    ["git", "add", "-A"],
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if add_result.returncode != 0:
                    return False, (add_result.stderr or add_result.stdout or "git add 执行失败。").strip()

                commit_message = self.suggest_commit_message(status_lines)
                commit_result = subprocess.run(
                    ["git", "commit", "-m", commit_message],
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if commit_result.returncode != 0:
                    return False, (commit_result.stderr or commit_result.stdout or "git commit 执行失败。").strip()

                head = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=repo_root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                commit_id = (head.stdout or "").strip()
                return True, f"代码已提交 {commit_id} · {commit_message}"

    def update_daily_changelog(self, repo_root: Path, status_lines: list[str]) -> None:
                changelog_path = repo_root / "changelog.md"
                today = datetime.now().astimezone().strftime("%Y-%m-%d")
                entry = f"- {current_local_time('%H:%M')} 提交代码：{self.summarize_changed_files(status_lines)}"
                if changelog_path.exists():
                    content = changelog_path.read_text(encoding="utf-8")
                else:
                    content = "# Changelog\n\n按日期记录项目功能与代码提交。\n"

                heading = f"## {today}"
                if heading not in content:
                    content = content.rstrip() + f"\n\n{heading}\n\n{entry}\n"
                else:
                    marker = f"{heading}\n"
                    content = content.replace(marker, f"{marker}\n{entry}\n", 1)
                changelog_path.write_text(content, encoding="utf-8")

    def summarize_changed_files(self, status_lines: list[str]) -> str:
                added: list[str] = []
                modified: list[str] = []
                deleted: list[str] = []
                for line in status_lines:
                    code = line[:2]
                    path_text = line[3:].strip()
                    if " -> " in path_text:
                        path_text = path_text.split(" -> ", 1)[1].strip()
                    label = Path(path_text).name or path_text
                    if "D" in code:
                        deleted.append(label)
                    elif "A" in code or code == "??":
                        added.append(label)
                    else:
                        modified.append(label)
                parts: list[str] = []
                if added:
                    parts.append(f"新增 {', '.join(added[:3])}" + (f" 等 {len(added)} 项" if len(added) > 3 else ""))
                if modified:
                    parts.append(f"更新 {', '.join(modified[:4])}" + (f" 等 {len(modified)} 项" if len(modified) > 4 else ""))
                if deleted:
                    parts.append(f"删除 {', '.join(deleted[:3])}" + (f" 等 {len(deleted)} 项" if len(deleted) > 3 else ""))
                return "；".join(parts) if parts else "更新项目文件"

    def suggest_commit_message(self, status_lines: list[str]) -> str:
                changed = " ".join(status_lines)
                topics: list[str] = []
                if any(token in changed for token in ("desktop_app", "desktop_app_", "packaging/", ".spec")):
                    topics.append("desktop app")
                if "README.md" in changed:
                    topics.append("docs")
                if "changelog.md" in changed:
                    topics.append("changelog")
                if not topics:
                    topics.append("project files")
                return "Update " + " and ".join(topics[:3])

    def clear_status_text(self) -> None:
                self.status_label.setText("")
                self.status_label.setProperty("tone", "")
                self.status_label.style().unpolish(self.status_label)
                self.status_label.style().polish(self.status_label)

    def current_effective_work_dir(self) -> Path:
                if not self.active_session_id:
                    return self.new_session_work_dir
                override = (self.session_work_dir_overrides.get(self.active_session_id) or "").strip()
                if override:
                    return Path(override).expanduser()
                session_cwd = load_session_cwd(self.config.codex_home, self.active_session_id)
                if session_cwd:
                    return Path(session_cwd).expanduser()
                return self.config.work_dir

    def update_work_dir_label(self) -> None:
                effective = self.current_effective_work_dir()
                display = truncate_text(str(effective), 34)
                self.work_dir_label.setText(f"工作目录 · {display}")
                self.work_dir_label.setToolTip(str(effective))

    def set_current_work_dir_override(self, work_dir: Path) -> None:
                work_dir = work_dir.expanduser().resolve()
                if self.active_session_id:
                    source_cwd = load_session_cwd(self.config.codex_home, self.active_session_id)
                    fallback = Path(source_cwd).expanduser() if source_cwd else self.config.work_dir
                    if work_dir == fallback:
                        self.session_work_dir_overrides.pop(self.active_session_id, None)
                    else:
                        self.session_work_dir_overrides[self.active_session_id] = str(work_dir)
                    save_session_work_dir_overrides(self.session_work_dir_overrides)
                else:
                    self.new_session_work_dir = work_dir
                    self.new_session_work_dir_overridden = work_dir != self.config.work_dir
                self.update_work_dir_label()

    def edit_current_work_dir(self) -> None:
                current = str(self.current_effective_work_dir())
                new_path, accepted = QInputDialog.getText(self, "修改工作目录", "工作目录", text=current)
                if not accepted:
                    return
                target = Path(new_path.strip()).expanduser()
                if not target.exists() or not target.is_dir():
                    QMessageBox.critical(self, "Codex for Linux", f"工作目录不存在：{target}")
                    return
                self.set_current_work_dir_override(target)
                self.set_status("工作目录已更新", "idle")

    def clear_layout_widgets(self, layout: QVBoxLayout | QHBoxLayout) -> None:
                while layout.count():
                    item = layout.takeAt(0)
                    widget = item.widget()
                    child_layout = item.layout()
                    if widget:
                        widget.deleteLater()
                    elif child_layout:
                        self.clear_layout_widgets(child_layout)

    def set_status(self, text: str, chip: str | None = None) -> None:
                compact = (text or "").strip()
                if hasattr(self, "status_clear_timer"):
                    self.status_clear_timer.stop()
                self.status_label.setText(compact)
                tone = "failure" if any(token in compact for token in ("失败", "错误")) else "success"
                self.status_label.setProperty("tone", tone if compact else "")
                self.status_label.style().unpolish(self.status_label)
                self.status_label.style().polish(self.status_label)
                if compact:
                    self.status_clear_timer.start(5000)
