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
