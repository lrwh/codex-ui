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
                self.status_label.setText(compact)
                tone = "failure" if any(token in compact for token in ("失败", "错误")) else "success"
                self.status_label.setProperty("tone", tone if compact else "")
                self.status_label.style().unpolish(self.status_label)
                self.status_label.style().polish(self.status_label)
                if chip:
                    self.header_status.setText(chip)
