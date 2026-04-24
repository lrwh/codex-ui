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

class CodexWorker(QThread):
    session_started = Signal(str)
    assistant_delta = Signal(str)
    assistant_message = Signal(str)
    usage_updated = Signal(dict)
    failed = Signal(str)
    finished_ok = Signal()

    def __init__(
        self,
        config: AppConfig,
        session_id: str | None,
        prompt: str,
        image_paths: list[str] | None = None,
        work_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.session_id = session_id
        self.prompt = prompt
        self.image_paths = image_paths or []
        self.work_dir = work_dir or config.work_dir
        self.proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self.requestInterruption()
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
                self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _pipe_reader(
        self,
        stream_name: str,
        stream: object,
        queue: Queue[tuple[str, str | None]],
    ) -> None:
        try:
            for line in stream:
                queue.put((stream_name, line))
        finally:
            queue.put((stream_name, None))

    def run(self) -> None:
        args = [self.config.codex_path, "exec"]
        args.append("--json")
        if self.config.sandbox_mode:
            args.extend(["--sandbox", self.config.sandbox_mode])
        if self.config.skip_git_repo_check:
            args.append("--skip-git-repo-check")
        if self.config.model:
            args.extend(["-m", self.config.model])
        if self.config.model_reasoning_effort:
            args.extend(["-c", f'model_reasoning_effort="{self.config.model_reasoning_effort}"'])
        if self.session_id:
            args.append("resume")
            for image_path in self.image_paths:
                args.extend(["-i", image_path])
            args.extend([self.session_id, self.prompt])
        else:
            for image_path in self.image_paths:
                args.extend(["-i", image_path])
            args.extend(["-C", str(self.work_dir)])
            args.append(self.prompt)

        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.work_dir,
            )
        except OSError as exc:
            self.failed.emit(str(exc))
            return

        proc = self.proc
        assert proc is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        emitted_messages: set[str] = set()
        queue: Queue[tuple[str, str | None]] = Queue()
        stdout_reader = Thread(
            target=self._pipe_reader,
            args=("stdout", proc.stdout, queue),
            daemon=True,
        )
        stderr_reader = Thread(
            target=self._pipe_reader,
            args=("stderr", proc.stderr, queue),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()
        open_streams = 2
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        while open_streams > 0:
            if self.isInterruptionRequested():
                self.stop()
                return
            try:
                stream_name, line = queue.get(timeout=1)
            except Empty:
                continue

            if line is None:
                open_streams -= 1
                continue

            stripped = line.strip()
            if stream_name == "stderr":
                if stripped:
                    stderr_chunks.append(stripped)
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                if stripped:
                    stdout_chunks.append(stripped)
                continue
            error_text = extract_error_text_from_event(item)
            if error_text:
                stdout_chunks.append(error_text)
            msg_type = item.get("type")
            delta_text = extract_stream_delta_text(item)
            if delta_text:
                self.assistant_delta.emit(delta_text)
            if msg_type == "thread.started":
                self.session_started.emit(item.get("thread_id", ""))
            elif msg_type == "item.completed":
                text = extract_assistant_text_from_item(item.get("item", {}))
                if text and text not in emitted_messages:
                    emitted_messages.add(text)
                    self.assistant_message.emit(text)
            elif msg_type == "response_item":
                payload = item.get("payload", {})
                text = extract_assistant_text_from_item(payload)
                if text and text not in emitted_messages:
                    emitted_messages.add(text)
                    self.assistant_message.emit(text)
            elif msg_type == "turn.completed":
                self.usage_updated.emit(item.get("usage", {}))

        code = proc.wait()
        if code != 0:
            err = "\n".join(stdout_chunks + stderr_chunks).strip() or f"codex exited with {code}"
            if self.isInterruptionRequested():
                return
            self.failed.emit(err)
            return
        self.finished_ok.emit()


class AccountActionWorker(QThread):
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, config: AppConfig, executable: str, args: list[str], success_message: str) -> None:
        super().__init__()
        self.config = config
        self.executable = executable
        self.args = args
        self.success_message = success_message
        self.proc: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self.requestInterruption()
        if self.proc is None:
            return
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
                self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def run(self) -> None:
        try:
            self.proc = subprocess.Popen(
                [self.executable, *self.args],
                cwd=self.config.work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            self.failed.emit(str(exc))
            return
        proc = self.proc
        assert proc is not None
        stdout, stderr = proc.communicate()
        if self.isInterruptionRequested():
            return
        if proc.returncode != 0:
            error = (stderr or stdout or "账号操作失败").strip()
            self.failed.emit(error)
            return
        self.finished_ok.emit(self.success_message)
