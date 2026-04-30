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

@dataclass
class AppConfig:
    codex_path: str
    codex_home: Path
    work_dir: Path
    model: str
    model_reasoning_effort: str
    full_auto: bool
    approval_policy: str
    sandbox_mode: str
    skip_git_repo_check: bool
    recent_session_limit: int
    input_method_strategy: str


@dataclass
class SessionSummary:
    session_id: str
    thread_name: str
    updated_at: str
    updated_at_raw: str


@dataclass
class SessionCandidate:
    session_id: str
    thread_name: str
    updated_at_raw: str
    title_priority: int


@dataclass
class ChatMessage:
    role: str
    text: str
    timestamp: str


@dataclass
class ConversationCacheEntry:
    session_path: str
    mtime_ns: int
    messages: list[ChatMessage]


@dataclass
class AttachmentInfo:
    path: str
    kind: str


@dataclass
class AccountInfo:
    account_key: str
    email: str
    display_name: str


@dataclass
class LocalAccountInfo:
    account_key: str
    email: str
    display_name: str
    plan: str
    auth_mode: str
    last_session_id: str
    usage_summary: str
    usage_summary_html: str
    usage_detail: str
    is_active: bool


@dataclass
class ReleaseAssetInfo:
    name: str
    download_url: str
    size: int


@dataclass
class ReleaseInfo:
    tag_name: str
    version: str
    title: str
    html_url: str
    body: str
    published_at: str
    assets: list[ReleaseAssetInfo]


DEFAULT_MODEL_CHOICES = ["", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"]
DEFAULT_REASONING_EFFORT_CHOICES = [
    ("默认", ""),
    ("低", "low"),
    ("中", "medium"),
    ("高", "high"),
    ("极高", "xhigh"),
]


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def model_display_name(model: str) -> str:
    model = (model or "").strip()
    if not model:
        return "默认"
    return model


def model_choices(current_model: str = "") -> list[str]:
    choices = DEFAULT_MODEL_CHOICES[:]
    current = (current_model or "").strip()
    if current and current not in choices:
        choices.insert(1, current)
    return choices


def normalize_reasoning_effort(value: object) -> str:
    effort = str(value or "").strip().lower()
    aliases = {
        "default": "",
        "reset": "",
        "none": "",
        "默认": "",
        "低": "low",
        "中": "medium",
        "高": "high",
        "极高": "xhigh",
    }
    effort = aliases.get(effort, effort)
    valid = {item[1] for item in DEFAULT_REASONING_EFFORT_CHOICES}
    if effort in valid:
        return effort
    return ""


def reasoning_effort_display_name(effort: str) -> str:
    effort = normalize_reasoning_effort(effort)
    for label, value in DEFAULT_REASONING_EFFORT_CHOICES:
        if value == effort:
            return label
    return "默认"


def humanize_count(value: object) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number < 1_000:
        return str(number)
    if number < 1_000_000:
        return f"{number / 1_000:.1f}K"
    if number < 1_000_000_000:
        return f"{number / 1_000_000:.1f}M"
    return f"{number / 1_000_000_000:.1f}B"


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
TEXT_ATTACHMENT_SUFFIXES = {".log", ".md", ".markdown"}
TEXT_ATTACHMENT_CHAR_LIMIT = 50000
APP_ICON_NAME = "codex-ui.svg"
APP_VERSION_FILE = "VERSION"
APP_RELEASE_REPO = "lrwh/codex-ui"
INITIAL_CONVERSATION_RENDER_LIMIT = 80
CONVERSATION_RENDER_CHUNK_SIZE = 80
MESSAGE_RENDER_BATCH_SIZE = 24
_MERGED_SESSION_CANDIDATE_CACHE: dict[str, dict[str, "SessionCandidate"]] = {}


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
        exe_dir = Path(sys.executable).resolve().parent
        internal_dir = exe_dir / "_internal"
        if internal_dir.is_dir():
            return internal_dir
        return exe_dir
    return Path(__file__).resolve().parent


def app_resource_path(name: str) -> Path:
    return app_base_dir() / name


def load_app_version() -> str:
    version_path = app_resource_path(APP_VERSION_FILE)
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    return version or "0.0.0"


def normalize_release_version(tag_name: str) -> str:
    return str(tag_name or "").strip().lstrip("vV")


def version_key(value: str) -> tuple[int, ...]:
    normalized = normalize_release_version(value)
    if not normalized:
        return (0,)
    values: list[int] = []
    for chunk in normalized.split("."):
        match = re.match(r"(\d+)", chunk)
        values.append(int(match.group(1)) if match else 0)
    return tuple(values)


def is_newer_version(latest: str, current: str) -> bool:
    return version_key(latest) > version_key(current)


def preferred_release_asset(release: ReleaseInfo) -> ReleaseAssetInfo | None:
    for suffix in (".deb", ".tar.gz"):
        for asset in release.assets:
            if asset.name.endswith(suffix):
                return asset
    return release.assets[0] if release.assets else None


def load_app_icon() -> QIcon | None:
    icon_path = app_resource_path(APP_ICON_NAME)
    if not icon_path.is_file():
        return None
    return QIcon(str(icon_path))


def detect_attachment_kind(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in TEXT_ATTACHMENT_SUFFIXES:
        return "text"
    return None


def attachment_label(path: str) -> str:
    return Path(path).name


def clipboard_attachment_dir() -> Path:
    path = ui_state_dir() / "clipboard-attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_attachment_summary(attachments: list[AttachmentInfo]) -> str:
    if not attachments:
        return ""
    lines = ["附件:"]
    for item in attachments:
        kind = "图片" if item.kind == "image" else "文本"
        lines.append(f"- {attachment_label(item.path)} ({kind})")
    return "\n".join(lines)


def read_text_attachment(path: str) -> str:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    if len(raw) <= TEXT_ATTACHMENT_CHAR_LIMIT:
        return raw
    return raw[:TEXT_ATTACHMENT_CHAR_LIMIT] + "\n\n[已截断，超过显示上限]"


def build_prompt_with_attachments(prompt: str, attachments: list[AttachmentInfo]) -> str:
    parts: list[str] = []
    base = (prompt or "").strip()
    if base:
        parts.append(base)
    text_attachments = [item for item in attachments if item.kind == "text"]
    if text_attachments:
        sections = ["以下是附加文件内容，请结合这些内容一起处理当前请求。"]
        for item in text_attachments:
            name = attachment_label(item.path)
            suffix = Path(item.path).suffix.lower()
            fence = "markdown" if suffix in {".md", ".markdown"} else "text"
            sections.append(
                f"[附件: {name}]\n"
                f"路径: {item.path}\n"
                f"```{fence}\n{read_text_attachment(item.path)}\n```"
            )
        parts.append("\n\n".join(sections))
    image_attachments = [item for item in attachments if item.kind == "image"]
    if image_attachments:
        image_lines = ["已附带图片附件，请结合图片内容一起处理当前请求："]
        for item in image_attachments:
            image_lines.append(f"- {attachment_label(item.path)}")
        parts.append("\n".join(image_lines))
    return "\n\n".join(part for part in parts if part.strip()).strip()


def sanitize_session_title(text: str) -> str:
    compact = " ".join((text or "").split()).strip()
    if not compact:
        return ""
    internal_prefixes = (
        "<turn_aborted>",
        "<environment_context>",
        "# AGENTS.md instructions",
        "<INSTRUCTIONS>",
        "<permissions instructions>",
    )
    if any(compact.startswith(prefix) for prefix in internal_prefixes):
        return ""
    return compact[:120]


def highlight_match(text: str, query: str) -> str:
    if not query:
        return text
    match = re.search(re.escape(query), text, flags=re.IGNORECASE)
    if not match:
        return text
    start, end = match.span()
    return (
        f"{text[:start]}"
        f"<span style='background-color:#efe2c8; color:#2c241d; border-radius:4px;'>{text[start:end]}</span>"
        f"{text[end:]}"
    )


def session_group_label(raw: str) -> str:
    dt = parse_timestamp(raw)
    if not dt:
        return "更早"
    local_date = dt.astimezone().date()
    today = datetime.now().astimezone().date()
    if local_date == today:
        return "今天"
    if (today - local_date).days == 1:
        return "昨天"
    return "更早"


def render_inline_markdown(text: str) -> str:
    pattern = re.compile(
        r"`([^`]+)`|\[([^\]]+)\]\((https?://[^\s)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*"
    )
    parts: list[str] = []
    last_end = 0
    for match in pattern.finditer(text):
        parts.append(html.escape(text[last_end:match.start()]))
        code_text, link_text, link_url, bold_text, italic_text = match.groups()
        if code_text is not None:
            parts.append(
                "<code style='background:#f3e8d8; color:#734d2b; padding:1px 6px; "
                "border-radius:6px; font-family:monospace;'>"
                f"{html.escape(code_text)}</code>"
            )
        elif link_text is not None and link_url is not None:
            parts.append(
                f"<a href='{html.escape(link_url, quote=True)}' "
                "style='color:#2f7b68; text-decoration:none;'>"
                f"{html.escape(link_text)}</a>"
            )
        elif bold_text is not None:
            parts.append(f"<strong>{html.escape(bold_text)}</strong>")
        elif italic_text is not None:
            parts.append(f"<em>{html.escape(italic_text)}</em>")
        last_end = match.end()
    parts.append(html.escape(text[last_end:]))
    return "".join(parts)


def render_markdown_html(text: str) -> str:
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            language_badge = (
                f"<div style='color:#9a7b5d; font-size:11px; margin-bottom:6px;'>{html.escape(language)}</div>"
                if language
                else ""
            )
            blocks.append(
                "<div style='margin:8px 0;'>"
                f"{language_badge}"
                "<pre style='margin:0; white-space:pre-wrap; word-break:break-word; "
                "background:#fff7eb; border:1px solid #ead9c2; border-radius:12px; padding:12px; "
                "font-family:monospace; font-size:12px; "
                "line-height:1.55; color:#3a2d23;'>"
                f"{html.escape(chr(10).join(code_lines))}</pre></div>"
            )
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            size_map = {1: 20, 2: 17, 3: 15, 4: 14}
            blocks.append(
                f"<div style='margin:8px 0 4px 0; font-weight:700; color:#2f241c; "
                f"font-size:{size_map.get(level, 14)}px;'>"
                f"{render_inline_markdown(heading.group(2))}</div>"
            )
            i += 1
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(
                "<blockquote style='margin:8px 0; padding:2px 0 2px 12px; border-left:3px solid #dcc5a7; "
                "color:#6d5847;'>"
                + "<br>".join(render_inline_markdown(item) for item in quote_lines)
                + "</blockquote>"
            )
            continue

        if re.match(r"^[-*]\s+", stripped):
            items: list[str] = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            blocks.append(
                "<ul style='margin:8px 0 8px 18px; padding:0; color:#2f241c;'>"
                + "".join(
                    f"<li style='margin:4px 0;'>{render_inline_markdown(item)}</li>" for item in items
                )
                + "</ul>"
            )
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            blocks.append(
                "<ol style='margin:8px 0 8px 20px; padding:0; color:#2f241c;'>"
                + "".join(
                    f"<li style='margin:4px 0;'>{render_inline_markdown(item)}</li>" for item in items
                )
                + "</ol>"
            )
            continue

        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            candidate = lines[i].strip()
            if (
                not candidate
                or candidate.startswith("```")
                or candidate.startswith(">")
                or re.match(r"^(#{1,4})\s+.+$", candidate)
                or re.match(r"^[-*]\s+", candidate)
                or re.match(r"^\d+\.\s+", candidate)
            ):
                break
            paragraph_lines.append(candidate)
            i += 1

        blocks.append(
            "<p style='margin:8px 0; color:#2f241c; line-height:1.65;'>"
            + "<br>".join(render_inline_markdown(item) for item in paragraph_lines)
            + "</p>"
        )

    if not blocks:
        return "<p style='margin:0; color:#2f241c; line-height:1.65;'></p>"
    return "".join(blocks)


def to_local_time(raw: str, fmt: str, fallback: str = "") -> str:
    if not raw:
        return fallback
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        return dt.astimezone().strftime(fmt)
    except ValueError:
        return fallback or raw


def current_local_time(fmt: str = "%H:%M") -> str:
    return datetime.now().astimezone().strftime(fmt)


def current_local_iso() -> str:
    return datetime.now().astimezone().isoformat()


def epoch_seconds_to_local_time(raw: object, fmt: str = "%m-%d %H:%M") -> str:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value).astimezone().strftime(fmt)


def format_remaining_usage(window: dict | None, label: str) -> str:
    if not isinstance(window, dict):
        return ""
    try:
        used_percent = int(window.get("used_percent", 0))
    except (TypeError, ValueError):
        return ""
    remaining = max(0, 100 - used_percent)
    return f"{label}{remaining}%"


def usage_color(remaining: int) -> str:
    if remaining <= 10:
        return "#b0523a"
    if remaining <= 30:
        return "#c7742f"
    return "#2f7b68"


def build_usage_badge_html(window: dict | None, label: str) -> str:
    if not isinstance(window, dict):
        return ""
    try:
        used_percent = int(window.get("used_percent", 0))
    except (TypeError, ValueError):
        return ""
    remaining = max(0, 100 - used_percent)
    color = usage_color(remaining)
    return f"<span style='color:{color}; font-weight:700;'>{label}{remaining}%</span>"


def build_usage_reset_html(window: dict | None) -> str:
    if not isinstance(window, dict):
        return ""
    reset_at = epoch_seconds_to_local_time(window.get("resets_at"), "%m-%d %H:%M")
    if not reset_at:
        return ""
    return f"<span style='color:#8d7763;'>@ {reset_at}</span>"


def format_remaining_usage_detail(window: dict | None, label: str) -> str:
    if not isinstance(window, dict):
        return ""
    try:
        used_percent = int(window.get("used_percent", 0))
    except (TypeError, ValueError):
        return ""
    remaining = max(0, 100 - used_percent)
    reset_at = epoch_seconds_to_local_time(window.get("resets_at"), "%m-%d %H:%M")
    if reset_at:
        return f"{label}剩余 {remaining}%，{reset_at} 重置"
    return f"{label}剩余 {remaining}%"


def build_account_usage_summary(last_usage: dict | None) -> str:
    if not isinstance(last_usage, dict):
        return ""
    primary = format_remaining_usage(last_usage.get("primary"), "5h ")
    secondary = format_remaining_usage(last_usage.get("secondary"), "周 ")
    parts = [item for item in (primary, secondary) if item]
    return " · ".join(parts)


def build_account_usage_summary_html(last_usage: dict | None) -> str:
    if not isinstance(last_usage, dict):
        return ""
    primary_badge = build_usage_badge_html(last_usage.get("primary"), "5h ")
    primary_reset = build_usage_reset_html(last_usage.get("primary"))
    secondary_badge = build_usage_badge_html(last_usage.get("secondary"), "周 ")
    secondary_reset = build_usage_reset_html(last_usage.get("secondary"))
    parts = []
    if primary_badge:
        parts.append(" ".join(item for item in (primary_badge, primary_reset) if item))
    if secondary_badge:
        parts.append(" ".join(item for item in (secondary_badge, secondary_reset) if item))
    if not parts:
        return ""
    return "<span style='color:#8d7763;'>剩余</span> " + " <span style='color:#b9a28a;'>·</span> ".join(parts)


def build_account_usage_detail(last_usage: dict | None) -> str:
    if not isinstance(last_usage, dict):
        return ""
    primary = format_remaining_usage_detail(last_usage.get("primary"), "5h ")
    secondary = format_remaining_usage_detail(last_usage.get("secondary"), "周 ")
    parts = [item for item in (primary, secondary) if item]
    return " · ".join(parts)


def bundled_qt_input_context_keys() -> set[str]:
    plugin_root = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins" / "platforminputcontexts"
    keys: set[str] = set()
    if not plugin_root.exists():
        return keys
    for path in plugin_root.glob("*.so"):
        name = path.name
        if "fcitx" in name:
            keys.add("fcitx")
        if "ibus" in name:
            keys.add("ibus")
        if "compose" in name:
            keys.add("xim")
    return keys


def ensure_ibus_daemon_started() -> None:
    if not shutil.which("ibus-daemon"):
        return
    try:
        running = subprocess.run(
            ["pgrep", "-x", "ibus-daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if running.returncode == 0:
            return
        subprocess.Popen(
            ["ibus-daemon", "-drx"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return


def normalize_input_method_strategy(value: object) -> str:
    strategy = str(value or "auto").strip().lower()
    if strategy in {"auto", "system", "fcitx", "ibus", "xim"}:
        return strategy
    return "auto"


def normalize_approval_policy(value: object) -> str:
    policy = str(value or "on-request").strip().lower()
    if policy in {"untrusted", "on-request", "never"}:
        return policy
    return "on-request"


def normalize_sandbox_mode(value: object) -> str:
    mode = str(value or "workspace-write").strip().lower()
    if mode in {"read-only", "workspace-write", "danger-full-access"}:
        return mode
    return "workspace-write"


def permission_preset_from_runtime(approval_policy: object, sandbox_mode: object) -> str:
    policy = normalize_approval_policy(approval_policy)
    mode = normalize_sandbox_mode(sandbox_mode)
    if policy == "untrusted" and mode == "read-only":
        return "readonly"
    if policy == "never" and mode == "danger-full-access":
        return "full"
    return "workspace"


def runtime_from_permission_preset(preset: object) -> tuple[str, str]:
    key = str(preset or "workspace").strip().lower()
    mapping = {
        "workspace": ("on-request", "workspace-write"),
        "readonly": ("untrusted", "read-only"),
        "full": ("never", "danger-full-access"),
    }
    return mapping.get(key, ("on-request", "workspace-write"))


def setup_qt_input_method_env(strategy: str = "auto") -> None:
    strategy = normalize_input_method_strategy(strategy)
    xmods = os.environ.get("XMODIFIERS", "")
    current = os.environ.get("QT_IM_MODULE", "")
    bundled_keys = bundled_qt_input_context_keys()

    if strategy == "system":
        return
    if strategy == "fcitx":
        os.environ["QT_IM_MODULE"] = "fcitx"
        os.environ["GTK_IM_MODULE"] = "fcitx"
        os.environ["XMODIFIERS"] = "@im=fcitx"
        return
    if strategy == "ibus":
        os.environ["QT_IM_MODULE"] = "ibus"
        os.environ["GTK_IM_MODULE"] = "ibus"
        os.environ["XMODIFIERS"] = "@im=ibus"
        ensure_ibus_daemon_started()
        return
    if strategy == "xim":
        os.environ["QT_IM_MODULE"] = "xim"
        os.environ["XMODIFIERS"] = xmods or "@im=fcitx"
        return

    if "fcitx" in xmods or current == "fcitx":
        # pip-installed PySide6 commonly lacks a compatible fcitx plugin but does ship ibus.
        if "fcitx" in bundled_keys:
            os.environ["QT_IM_MODULE"] = "fcitx"
            os.environ["GTK_IM_MODULE"] = "fcitx"
            os.environ["XMODIFIERS"] = "@im=fcitx"
        elif "ibus" in bundled_keys:
            os.environ["QT_IM_MODULE"] = "ibus"
            os.environ["GTK_IM_MODULE"] = "ibus"
            os.environ["XMODIFIERS"] = "@im=ibus"
            ensure_ibus_daemon_started()
        else:
            os.environ["QT_IM_MODULE"] = "xim"
            os.environ["XMODIFIERS"] = "@im=fcitx"
        return

    if current:
        if current == "ibus":
            os.environ["GTK_IM_MODULE"] = "ibus"
            os.environ["XMODIFIERS"] = "@im=ibus"
            ensure_ibus_daemon_started()
        return
    if "ibus" in xmods:
        os.environ["QT_IM_MODULE"] = "ibus"
        os.environ["GTK_IM_MODULE"] = "ibus"
        os.environ["XMODIFIERS"] = "@im=ibus"
        ensure_ibus_daemon_started()


def normalize_timestamp(raw: str) -> str:
    if not raw:
        return ""
    return raw[:-1] + "+00:00" if raw.endswith("Z") else raw


def parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(normalize_timestamp(raw))
    except ValueError:
        return None


def extract_content_text(content: list[dict] | None) -> str:
    if not content:
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text", "")).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def extract_assistant_text_from_item(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    item_type = item.get("type")
    if item_type == "agent_message":
        return str(item.get("text", "")).strip()
    if item_type == "message" and item.get("role") == "assistant":
        return extract_content_text(item.get("content"))
    return ""


def extract_stream_delta_text(event: dict) -> str:
    if not isinstance(event, dict):
        return ""
    event_type = str(event.get("type") or "")
    method = str(event.get("method") or "")

    direct_delta_types = {
        "agent_message_delta",
        "agent_message_content_delta",
        "response.output_text.delta",
        "output_text.delta",
    }
    if event_type in direct_delta_types:
        return str(event.get("delta") or event.get("text") or "").strip()

    if method in {"item/agentMessage/delta", "item/agentMessageContent/delta"}:
        params = event.get("params", {})
        if isinstance(params, dict):
            return str(params.get("delta") or params.get("text") or "").strip()

    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type in direct_delta_types:
            return str(payload.get("delta") or payload.get("text") or "").strip()

    return ""


def extract_error_text_from_event(event: dict) -> str:
    if not isinstance(event, dict):
        return ""

    event_type = str(event.get("type") or "")
    error_like_types = {
        "error",
        "turn.failed",
        "response.failed",
        "response.error",
        "exec.error",
    }

    direct_parts: list[str] = []
    for key in ("message", "error", "detail", "reason"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            direct_parts.append(value.strip())
        elif isinstance(value, dict):
            for nested_key in ("message", "error", "detail", "reason"):
                nested_value = value.get(nested_key)
                if isinstance(nested_value, str) and nested_value.strip():
                    direct_parts.append(nested_value.strip())

    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type in error_like_types:
            for key in ("message", "error", "detail", "reason"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    direct_parts.append(value.strip())

    if event_type in error_like_types or direct_parts:
        return "\n".join(dict.fromkeys(direct_parts)).strip()
    return ""


def ui_state_dir() -> Path:
    path = Path.home() / ".config" / "codex-ui"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return ui_state_dir() / "config.json"


def session_account_map_path() -> Path:
    return ui_state_dir() / "session_accounts.json"


def pinned_sessions_path() -> Path:
    return ui_state_dir() / "pinned_sessions.json"


def session_aliases_path() -> Path:
    return ui_state_dir() / "session_aliases.json"


def session_work_dir_overrides_path() -> Path:
    return ui_state_dir() / "session_work_dirs.json"


def extract_session_id_from_rollout(path: str) -> str | None:
    match = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", path)
    return match.group(1) if match else None


def session_sort_key(raw: str) -> datetime:
    return parse_timestamp(raw) or datetime.min


def is_fallback_thread_name(thread_name: str, session_id: str) -> bool:
    stripped = (thread_name or "").strip()
    return not stripped or stripped == session_id or stripped == session_id[:8]


def build_session_summary(
    session_id: str,
    thread_name: str,
    updated_at_raw: str,
    aliases: dict[str, str] | None = None,
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        thread_name=apply_session_alias(session_id, thread_name, aliases),
        updated_at=to_local_time(updated_at_raw, "%m-%d %H:%M"),
        updated_at_raw=updated_at_raw,
    )


def build_session_candidate(
    session_id: str,
    thread_name: str,
    updated_at_raw: str,
    title_priority: int,
) -> SessionCandidate:
    cleaned_title = sanitize_session_title(thread_name)
    return SessionCandidate(
        session_id=session_id,
        thread_name=cleaned_title or session_id[:8],
        updated_at_raw=updated_at_raw,
        title_priority=title_priority if cleaned_title else 0,
    )


def resolve_codex_auth_path(config: AppConfig) -> str:
    codex_path = Path(config.codex_path).expanduser()
    sibling = codex_path.parent / "codex-auth"
    if sibling.exists():
        return str(sibling)
    return shutil.which("codex-auth") or "codex-auth"


def save_config(config: AppConfig) -> None:
    payload = {
        "codex_path": config.codex_path,
        "codex_home": str(config.codex_home),
        "work_dir": str(config.work_dir),
        "model": config.model,
        "model_reasoning_effort": config.model_reasoning_effort,
        "full_auto": config.full_auto,
        "approval_policy": config.approval_policy,
        "sandbox_mode": config.sandbox_mode,
        "skip_git_repo_check": config.skip_git_repo_check,
        "recent_session_limit": config.recent_session_limit,
        "input_method_strategy": config.input_method_strategy,
    }
    config_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_session_candidate(current: SessionCandidate | None, incoming: SessionCandidate) -> SessionCandidate:
    if current is None:
        return incoming

    latest_raw = (
        incoming.updated_at_raw
        if session_sort_key(incoming.updated_at_raw) >= session_sort_key(current.updated_at_raw)
        else current.updated_at_raw
    )

    current_title = current.thread_name.strip()
    incoming_title = incoming.thread_name.strip()
    title = current_title
    priority = current.title_priority

    if incoming.title_priority > current.title_priority and incoming_title:
        title = incoming_title
        priority = incoming.title_priority
    elif incoming.title_priority == current.title_priority:
        if is_fallback_thread_name(current_title, current.session_id) and incoming_title:
            title = incoming_title
            priority = incoming.title_priority
        elif current_title and incoming_title and len(incoming_title) < len(current_title):
            title = incoming_title
            priority = incoming.title_priority

    return SessionCandidate(
        session_id=current.session_id,
        thread_name=title or current.session_id[:8],
        updated_at_raw=latest_raw,
        title_priority=priority,
    )


def load_account_registry(codex_home: Path) -> dict:
    path = codex_home / "accounts" / "registry.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_account_registry(codex_home: Path, registry: dict) -> None:
    path = codex_home / "accounts" / "registry.json"
    if path.exists():
        backup = path.with_name(f"registry.json.bak.{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}")
        try:
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def switch_active_account_local(codex_home: Path, account_key: str) -> bool:
    registry = load_account_registry(codex_home)
    accounts = registry.get("accounts", [])
    if not any((item.get("account_key") or "") == account_key for item in accounts):
        return False
    if registry.get("active_account_key") == account_key:
        return True
    registry["active_account_key"] = account_key
    registry["active_account_activated_at_ms"] = int(datetime.now().timestamp() * 1000)
    save_account_registry(codex_home, registry)
    return True


def load_pinned_session_ids() -> set[str]:
    path = pinned_sessions_path()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    items = data.get("session_ids", [])
    if not isinstance(items, list):
        return set()
    return {str(item) for item in items if item}


def save_pinned_session_ids(session_ids: set[str]) -> None:
    path = pinned_sessions_path()
    payload = {
        "session_ids": sorted(session_ids),
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session_aliases() -> dict[str, str]:
    path = session_aliases_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    aliases = data.get("aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {str(k): str(v).strip() for k, v in aliases.items() if str(v).strip()}


def save_session_aliases(aliases: dict[str, str]) -> None:
    path = session_aliases_path()
    payload = {"aliases": aliases, "updated_at": datetime.now().astimezone().isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session_work_dir_overrides() -> dict[str, str]:
    path = session_work_dir_overrides_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    mappings = data.get("work_dirs", {})
    if not isinstance(mappings, dict):
        return {}
    cleaned: dict[str, str] = {}
    for session_id, raw_path in mappings.items():
        session_key = str(session_id).strip()
        path_text = str(raw_path).strip()
        if session_key and path_text:
            cleaned[session_key] = path_text
    return cleaned


def save_session_work_dir_overrides(mappings: dict[str, str]) -> None:
    path = session_work_dir_overrides_path()
    payload = {"work_dirs": mappings, "updated_at": datetime.now().astimezone().isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def session_cwd_from_path(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = item.get("payload", {})
                item_type = item.get("type")
                if item_type in {"session_meta", "turn_context"} and isinstance(payload, dict):
                    cwd = str(payload.get("cwd") or "").strip()
                    if cwd:
                        return cwd
    except OSError:
        return ""
    return ""


def load_session_cwd(codex_home: Path, session_id: str) -> str:
    return session_cwd_from_path(find_session_file(codex_home, session_id))


def apply_session_alias(session_id: str, thread_name: str, aliases: dict[str, str] | None = None) -> str:
    if aliases:
        alias = (aliases.get(session_id) or "").strip()
        if alias:
            return alias
    return thread_name or session_id[:8]


def load_active_account(config: AppConfig) -> AccountInfo | None:
    registry = load_account_registry(config.codex_home)
    active_key = registry.get("active_account_key")
    if not active_key:
        return None
    for account in registry.get("accounts", []):
        if account.get("account_key") != active_key:
            continue
        email = account.get("email") or ""
        display_name = (
            account.get("alias")
            or account.get("account_name")
            or email
            or account.get("chatgpt_user_id")
            or active_key
        )
        return AccountInfo(account_key=active_key, email=email, display_name=display_name)
    return AccountInfo(account_key=active_key, email="", display_name=active_key)


def load_all_accounts(config: AppConfig) -> list[LocalAccountInfo]:
    registry = load_account_registry(config.codex_home)
    active_key = str(registry.get("active_account_key") or "")
    accounts: list[LocalAccountInfo] = []
    for account in registry.get("accounts", []):
        account_key = str(account.get("account_key") or "").strip()
        if not account_key:
            continue
        email = str(account.get("email") or "").strip()
        display_name = (
            str(account.get("alias") or "").strip()
            or str(account.get("account_name") or "").strip()
            or email
            or str(account.get("chatgpt_user_id") or "").strip()
            or account_key
        )
        last_rollout = account.get("last_local_rollout") or {}
        last_session_id = extract_session_id_from_rollout(str(last_rollout.get("path") or "")) or ""
        accounts.append(
            LocalAccountInfo(
                account_key=account_key,
                email=email,
                display_name=display_name,
                plan=str(account.get("plan") or ""),
                auth_mode=str(account.get("auth_mode") or ""),
                last_session_id=last_session_id,
                usage_summary=build_account_usage_summary(account.get("last_usage")),
                usage_summary_html=build_account_usage_summary_html(account.get("last_usage")),
                usage_detail=build_account_usage_detail(account.get("last_usage")),
                is_active=account_key == active_key,
            )
        )
    accounts.sort(key=lambda item: (not item.is_active, item.display_name.lower(), item.email.lower()))
    return accounts


def load_session_account_map(config: AppConfig) -> dict[str, str]:
    path = session_account_map_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    mappings = data.get("mappings", {})
    if isinstance(mappings, dict):
        return {str(k): str(v) for k, v in mappings.items()}
    return {}


def save_session_account_map(mappings: dict[str, str]) -> None:
    path = session_account_map_path()
    payload = {"mappings": mappings, "updated_at": datetime.now().astimezone().isoformat()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def seed_session_account_map(config: AppConfig, mappings: dict[str, str]) -> dict[str, str]:
    registry = load_account_registry(config.codex_home)
    changed = False
    for account in registry.get("accounts", []):
        rollout = (account.get("last_local_rollout") or {}).get("path", "")
        session_id = extract_session_id_from_rollout(rollout)
        account_key = account.get("account_key")
        if session_id and account_key and mappings.get(session_id) != account_key:
            mappings[session_id] = account_key
            changed = True
    if changed:
        save_session_account_map(mappings)
    return mappings


def latest_session_timestamp(codex_home: Path, session_id: str, fallback_raw: str) -> str:
    path = find_session_file(codex_home, session_id)
    if not path or not path.exists():
        return fallback_raw
    latest = parse_timestamp(fallback_raw)
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = parse_timestamp(item.get("timestamp", ""))
                if ts and (latest is None or ts > latest):
                    latest = ts
    except OSError:
        return fallback_raw
    return latest.isoformat() if latest else fallback_raw


def load_index_session_candidates(codex_home: Path) -> dict[str, SessionCandidate]:
    index_path = codex_home / "session_index.jsonl"
    if not index_path.exists():
        return {}

    sessions: dict[str, SessionCandidate] = {}
    with index_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = item.get("id")
            if not session_id:
                continue
            thread_name = (item.get("thread_name") or "").strip()
            candidate = build_session_candidate(
                session_id=session_id,
                thread_name=thread_name or session_id[:8],
                updated_at_raw=normalize_timestamp(item.get("updated_at", "")),
                title_priority=0 if is_fallback_thread_name(thread_name, session_id) else 2,
            )
            sessions[session_id] = merge_session_candidate(sessions.get(session_id), candidate)
    return sessions


def scan_session_file_candidate(path: Path) -> SessionCandidate | None:
    session_id = extract_session_id_from_rollout(path.name)
    thread_name = ""
    title_priority = 0
    first_user_message = ""
    response_user_message = ""
    latest_seen: datetime | None = None
    latest_raw = ""

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue

                timestamp = parse_timestamp(item.get("timestamp", ""))
                if timestamp and (latest_seen is None or timestamp > latest_seen):
                    latest_seen = timestamp
                    latest_raw = timestamp.isoformat()

                item_type = item.get("type")
                payload = item.get("payload", {})

                if item_type == "session_meta" and isinstance(payload, dict):
                    session_id = session_id or payload.get("id")
                    continue

                if item_type == "event_msg" and isinstance(payload, dict):
                    payload_type = payload.get("type")
                    if payload_type == "thread_name_updated":
                        candidate_name = str(payload.get("thread_name", "")).strip()
                        if candidate_name:
                            thread_name = candidate_name
                            title_priority = 3
                    elif payload_type == "user_message" and not first_user_message:
                        first_user_message = str(payload.get("message", "")).strip()
                    continue

                if (
                    item_type == "response_item"
                    and isinstance(payload, dict)
                    and payload.get("type") == "message"
                    and payload.get("role") == "user"
                    and not response_user_message
                ):
                    response_user_message = extract_content_text(payload.get("content"))
    except OSError:
        return None

    if not session_id:
        return None

    final_title = thread_name or first_user_message or response_user_message or session_id[:8]
    if title_priority == 0 and not is_fallback_thread_name(final_title, session_id):
        title_priority = 1

    return build_session_candidate(
        session_id=session_id,
        thread_name=final_title,
        updated_at_raw=latest_raw,
        title_priority=title_priority,
    )


def load_file_session_candidates(codex_home: Path) -> dict[str, SessionCandidate]:
    root = codex_home / "sessions"
    if not root.exists():
        return {}

    sessions: dict[str, SessionCandidate] = {}
    for path in root.rglob("*.jsonl"):
        candidate = scan_session_file_candidate(path)
        if not candidate:
            continue
        sessions[candidate.session_id] = merge_session_candidate(
            sessions.get(candidate.session_id),
            candidate,
        )
    return sessions


def invalidate_session_candidate_cache(codex_home: Path | None = None) -> None:
    if codex_home is None:
        _MERGED_SESSION_CANDIDATE_CACHE.clear()
        return
    _MERGED_SESSION_CANDIDATE_CACHE.pop(str(codex_home.resolve()), None)


def load_merged_session_candidates(codex_home: Path, force_refresh: bool = False) -> dict[str, SessionCandidate]:
    cache_key = str(codex_home.resolve())
    if not force_refresh:
        cached = _MERGED_SESSION_CANDIDATE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    merged = load_file_session_candidates(codex_home)
    for session_id, candidate in load_index_session_candidates(codex_home).items():
        merged[session_id] = merge_session_candidate(merged.get(session_id), candidate)
    _MERGED_SESSION_CANDIDATE_CACHE[cache_key] = merged
    return merged


def load_config() -> AppConfig:
    home = Path.home()
    default = {
        "codex_path": shutil.which("codex") or "codex",
        "codex_home": str(home / ".codex"),
        "work_dir": str(Path.cwd()),
        "model": "",
        "model_reasoning_effort": "",
        "full_auto": True,
        "approval_policy": "on-request",
        "sandbox_mode": "workspace-write",
        "skip_git_repo_check": True,
        "recent_session_limit": 30,
        "input_method_strategy": "auto",
    }
    path = config_path()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            user = json.load(f)
        default.update(user)
    return AppConfig(
        codex_path=default["codex_path"],
        codex_home=Path(default["codex_home"]).expanduser(),
        work_dir=Path(default["work_dir"]).expanduser(),
        model=default["model"],
        model_reasoning_effort=normalize_reasoning_effort(default.get("model_reasoning_effort")),
        full_auto=default["full_auto"],
        approval_policy=normalize_approval_policy(default.get("approval_policy")),
        sandbox_mode=normalize_sandbox_mode(default.get("sandbox_mode")),
        skip_git_repo_check=default["skip_git_repo_check"],
        recent_session_limit=int(default["recent_session_limit"]),
        input_method_strategy=normalize_input_method_strategy(default.get("input_method_strategy")),
    )


def load_sessions(
    config: AppConfig,
    account_key: str | None = None,
    session_account_map: dict[str, str] | None = None,
    session_aliases: dict[str, str] | None = None,
    force_refresh: bool = False,
) -> list[SessionSummary]:
    candidates = load_merged_session_candidates(config.codex_home, force_refresh=force_refresh)
    sessions = [
        build_session_summary(item.session_id, item.thread_name, item.updated_at_raw, session_aliases)
        for item in candidates.values()
    ]
    sessions.sort(key=lambda x: session_sort_key(x.updated_at_raw), reverse=True)
    if account_key and session_account_map:
        sessions = [s for s in sessions if session_account_map.get(s.session_id) == account_key]
    return sessions


def load_session_summary(
    config: AppConfig,
    session_id: str,
    session_aliases: dict[str, str] | None = None,
    force_refresh: bool = False,
) -> SessionSummary | None:
    candidate = load_merged_session_candidates(config.codex_home, force_refresh=force_refresh).get(session_id)
    if not candidate:
        return None
    return build_session_summary(candidate.session_id, candidate.thread_name, candidate.updated_at_raw, session_aliases)


def find_session_file(codex_home: Path, session_id: str) -> Path | None:
    root = codex_home / "sessions"
    if not root.exists():
        return None
    for path in root.rglob("*.jsonl"):
        if session_id in path.name:
            return path
    return None


def conversation_file_info(codex_home: Path, session_id: str) -> tuple[Path | None, int]:
    path = find_session_file(codex_home, session_id)
    if not path:
        return None, 0
    try:
        return path, path.stat().st_mtime_ns
    except OSError:
        return path, 0


def load_conversation_from_path(path: Path | None) -> list[ChatMessage]:
    if not path:
        return []
    messages: list[ChatMessage] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "response_item":
                continue
            payload = item.get("payload", {})
            if payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = extract_content_text(payload.get("content"))
            if not text:
                continue
            messages.append(
                ChatMessage(
                    role=role,
                    text=text,
                    timestamp=to_local_time(item.get("timestamp", ""), "%H:%M"),
                )
            )
    return messages
