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
from PySide6.QtGui import QCloseEvent, QFont, QGuiApplication, QIcon, QKeySequence, QShortcut, QTextCursor
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


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


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
INITIAL_CONVERSATION_RENDER_LIMIT = 120
CONVERSATION_RENDER_CHUNK_SIZE = 120


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_resource_path(name: str) -> Path:
    return app_base_dir() / name


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
                "border-radius:6px; font-family:\"JetBrains Mono\",\"Noto Sans Mono CJK SC\",monospace;'>"
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
                "font-family:\"JetBrains Mono\",\"Noto Sans Mono CJK SC\",monospace; font-size:12px; "
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


def load_merged_session_candidates(codex_home: Path) -> dict[str, SessionCandidate]:
    merged = load_file_session_candidates(codex_home)
    for session_id, candidate in load_index_session_candidates(codex_home).items():
        merged[session_id] = merge_session_candidate(merged.get(session_id), candidate)
    return merged


def load_config() -> AppConfig:
    home = Path.home()
    default = {
        "codex_path": shutil.which("codex") or "codex",
        "codex_home": str(home / ".codex"),
        "work_dir": str(Path.cwd()),
        "model": "",
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
) -> list[SessionSummary]:
    candidates = load_merged_session_candidates(config.codex_home)
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
) -> SessionSummary | None:
    candidate = load_merged_session_candidates(config.codex_home).get(session_id)
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

class CodexWorker(QThread):
    session_started = Signal(str)
    assistant_delta = Signal(str)
    assistant_message = Signal(str)
    usage_updated = Signal(dict)
    failed = Signal(str)
    finished_ok = Signal()

    def __init__(self, config: AppConfig, session_id: str | None, prompt: str, image_paths: list[str] | None = None) -> None:
        super().__init__()
        self.config = config
        self.session_id = session_id
        self.prompt = prompt
        self.image_paths = image_paths or []
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
        for image_path in self.image_paths:
            args.extend(["-i", image_path])
        if not self.session_id:
            args.extend(["-C", str(self.config.work_dir)])
        if self.session_id:
            args.append("resume")
        if self.session_id:
            args.extend([self.session_id, self.prompt])
        else:
            args.append(self.prompt)

        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.config.work_dir,
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

            if stream_name == "stderr":
                stripped = line.strip()
                if stripped:
                    stderr_chunks.append(stripped)
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
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
            err = "\n".join(stderr_chunks).strip() or f"codex exited with {code}"
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


class AccountDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("accountDialog")
        self.setWindowTitle("账号管理")
        self.setModal(True)
        self.resize(520, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("账号管理")
        title.setObjectName("pageTitle")
        subtitle = QLabel("切换当前活跃账号，或发起新的登录流程")
        subtitle.setObjectName("pageSubtitle")

        top = QVBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(2)
        top.addWidget(title)
        top.addWidget(subtitle)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.login_button = QPushButton("登录新账号")
        self.login_button.setObjectName("scopeButton")
        self.refresh_button = QPushButton("刷新用量")
        self.refresh_button.setObjectName("scopeButton")
        self.copy_button = QPushButton("复制当前账号")
        self.copy_button.setObjectName("scopeButton")
        self.open_dir_button = QPushButton("打开账号目录")
        self.open_dir_button.setObjectName("scopeButton")
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("scopeButton")
        self.login_button.clicked.connect(self.on_login)
        self.refresh_button.clicked.connect(self.on_refresh)
        self.copy_button.clicked.connect(self.on_copy_current)
        self.open_dir_button.clicked.connect(self.on_open_dir)
        self.close_button.clicked.connect(self.accept)
        action_row.addWidget(self.login_button, 0)
        action_row.addWidget(self.refresh_button, 0)
        action_row.addWidget(self.copy_button, 0)
        action_row.addWidget(self.open_dir_button, 0)
        action_row.addStretch(1)
        action_row.addWidget(self.close_button, 0)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("sidebarHint")

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)

        root.addLayout(top)
        root.addLayout(action_row)
        root.addWidget(self.hint_label)
        root.addWidget(self.list_host, 1)

        self.reload()

    def clear_rows(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            layout = item.layout()
            if widget:
                widget.deleteLater()
            elif layout:
                self.window.clear_layout_widgets(layout)

    def reload(self) -> None:
        self.window.all_accounts = load_all_accounts(self.window.config)
        self.clear_rows()
        has_auth_tool = bool(
            self.window.codex_auth_path
            and (Path(self.window.codex_auth_path).exists() or shutil.which(Path(self.window.codex_auth_path).name))
        )
        self.login_button.setEnabled(has_auth_tool and self.window.account_worker is None)
        self.refresh_button.setEnabled(self.window.account_worker is None)
        if self.window.account_worker is not None:
            self.hint_label.setText("账号操作进行中…")
        elif not has_auth_tool:
            self.hint_label.setText("未找到 codex-auth，仅支持本地账号切换")
        else:
            self.hint_label.setText(f"本地账号 {len(self.window.all_accounts)} 个")
        self.copy_button.setEnabled(bool(self.window.active_account))
        self.open_dir_button.setEnabled(True)

        for account in self.window.all_accounts:
            row = QFrame()
            row.setObjectName("accountCard")
            row.setProperty("active", account.is_active)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(10)

            text_layout = QVBoxLayout()
            text_layout.setContentsMargins(0, 0, 0, 0)
            text_layout.setSpacing(3)
            title = QLabel(account.display_name)
            title.setObjectName("accountTitle")
            meta_parts = []
            if account.email and account.email != account.display_name:
                meta_parts.append(account.email)
            if account.plan:
                meta_parts.append(account.plan)
            if account.auth_mode:
                meta_parts.append(account.auth_mode)
            if account.last_session_id:
                meta_parts.append(account.last_session_id[:8])
            meta = QLabel(" · ".join(meta_parts))
            meta.setObjectName("accountMeta")
            usage = QLabel()
            usage.setObjectName("accountUsage")
            usage.setTextFormat(Qt.RichText)
            usage.setText(account.usage_summary_html or "<span style='color:#8d7763;'>剩余用量暂不可用</span>")
            text_layout.addWidget(title)
            text_layout.addWidget(meta)
            text_layout.addWidget(usage)

            action = QPushButton("当前" if account.is_active else "切换")
            action.setObjectName("accountCurrentButton" if account.is_active else "accountSwitchButton")
            action.setEnabled((not account.is_active) and self.window.account_worker is None)
            action.clicked.connect(
                lambda _checked=False, account_key=account.account_key, label=account.display_name: self.on_switch(
                    account_key, label
                )
            )

            row_layout.addLayout(text_layout, 1)
            row_layout.addWidget(action, 0)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)

    def on_switch(self, account_key: str, label: str) -> None:
        self.window.switch_account(account_key, label)
        self.accept()

    def on_login(self) -> None:
        self.window.login_new_account()
        self.accept()

    def on_refresh(self) -> None:
        self.window.refresh_account_usage()

    def on_copy_current(self) -> None:
        self.window.copy_current_account_info()

    def on_open_dir(self) -> None:
        self.window.open_accounts_directory()


class SettingsDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setObjectName("accountDialog")
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(520, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        subtitle = QLabel("修改新会话默认参数和客户端运行配置")
        subtitle.setObjectName("pageSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        self.work_dir_input = QLineEdit(str(window.config.work_dir))
        self.work_dir_input.setObjectName("searchInput")
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setObjectName("searchInput")
        self.model_combo.addItems(["", "gpt-5.4", "gpt-5.4-mini", "gpt-5.2"])
        self.model_combo.setCurrentText(window.config.model)
        self.codex_path_input = QLineEdit(window.config.codex_path)
        self.codex_path_input.setObjectName("searchInput")

        self.permission_mode_combo = QComboBox()
        self.permission_mode_combo.addItem("工作区", "workspace")
        self.permission_mode_combo.addItem("只读", "readonly")
        self.permission_mode_combo.addItem("全权限", "full")
        self.permission_mode_combo.setCurrentIndex(
            self.permission_mode_combo.findData(
                permission_preset_from_runtime(window.config.approval_policy, window.config.sandbox_mode)
            )
        )

        self.skip_git_check = QCheckBox("跳过 git 仓库检查")
        self.skip_git_check.setChecked(window.config.skip_git_repo_check)

        self.input_method_combo = QComboBox()
        self.input_method_combo.addItem("auto", "auto")
        self.input_method_combo.addItem("system", "system")
        self.input_method_combo.addItem("fcitx", "fcitx")
        self.input_method_combo.addItem("ibus", "ibus")
        self.input_method_combo.addItem("xim", "xim")
        self.input_method_combo.setCurrentText(window.config.input_method_strategy)

        fields = [
            ("工作目录", self.work_dir_input),
            ("模型", self.model_combo),
            ("权限模式", self.permission_mode_combo),
            ("Codex 路径", self.codex_path_input),
            ("输入法策略", self.input_method_combo),
        ]
        for label_text, widget in fields:
            label = QLabel(label_text)
            label.setObjectName("cardMeta")
            root.addWidget(label)
            root.addWidget(widget)

        root.addWidget(self.skip_git_check)

        hint = QLabel("权限模式会同步到输入区的权限选择；输入法策略保存后需重启应用才能完全生效。")
        hint.setObjectName("sidebarHint")
        root.addWidget(hint)
        root.addStretch(1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        cancel = QPushButton("取消")
        cancel.setObjectName("scopeButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.on_save)
        action_row.addStretch(1)
        action_row.addWidget(cancel, 0)
        action_row.addWidget(save, 0)
        root.addLayout(action_row)

    def on_save(self) -> None:
        work_dir = Path(self.work_dir_input.text().strip()).expanduser()
        if not work_dir.exists() or not work_dir.is_dir():
            QMessageBox.critical(self, "codex-ui", f"工作目录不存在：{work_dir}")
            return

        codex_path = self.codex_path_input.text().strip() or "codex"
        old_input_strategy = self.window.config.input_method_strategy
        approval_policy, sandbox_mode = runtime_from_permission_preset(self.permission_mode_combo.currentData())
        self.window.config = AppConfig(
            codex_path=codex_path,
            codex_home=self.window.config.codex_home,
            work_dir=work_dir,
            model=self.model_combo.currentText().strip(),
            full_auto=(approval_policy == "on-request" and sandbox_mode == "workspace-write"),
            approval_policy=approval_policy,
            sandbox_mode=sandbox_mode,
            skip_git_repo_check=self.skip_git_check.isChecked(),
            recent_session_limit=self.window.config.recent_session_limit,
            input_method_strategy=normalize_input_method_strategy(self.input_method_combo.currentText()),
        )
        save_config(self.window.config)
        self.window.apply_runtime_config(input_method_changed=old_input_strategy != self.window.config.input_method_strategy)
        self.accept()

class SessionGroupHeader(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("sessionGroupHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 2)
        label = QLabel(title)
        label.setObjectName("sessionGroupTitle")
        layout.addWidget(label)


class SessionListItem(QFrame):
    def __init__(self, session: SessionSummary, selected: bool, query: str = "") -> None:
        super().__init__()
        self.setObjectName("sessionCard")
        self.setProperty("selected", selected)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        dot = QLabel("●")
        dot.setObjectName("sessionDot")
        dot.setProperty("selected", selected)

        short_id = session.session_id[:8]
        title = QLabel(highlight_match(truncate_text(session.thread_name, 18), query))
        title.setObjectName("sessionTitle")
        title.setWordWrap(False)
        title.setTextFormat(Qt.RichText)

        top_row.addWidget(dot, 0, Qt.AlignTop)
        top_row.addWidget(title, 1)

        meta = QLabel(f"{session.updated_at} · {highlight_match(short_id, query)}")
        meta.setObjectName("sessionMeta")
        meta.setTextFormat(Qt.RichText)

        layout.addLayout(top_row)
        layout.addWidget(meta)


class MessageBubble(QFrame):
    def __init__(self, message: ChatMessage) -> None:
        super().__init__()
        self.message = message
        self.setObjectName("bubbleUser" if message.role == "user" else "bubbleAssistant")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        bubble = QFrame()
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 12, 16, 12)
        bubble_layout.setSpacing(6)
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bubble.setMaximumWidth(920)
        bubble.setMinimumWidth(700 if message.role == "assistant" else 520)

        header = QLabel(("你" if message.role == "user" else "Codex") + f"  {message.timestamp}")
        header.setObjectName("bubbleHeader")
        self.body = QLabel()
        self.body.setObjectName("bubbleBody")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setOpenExternalLinks(True)
        self.body.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard
        )
        self.body.setText(render_markdown_html(message.text))
        bubble_layout.addWidget(header)
        bubble_layout.addWidget(self.body)

        if message.role == "user":
            root.addStretch(1)
            root.addWidget(bubble, 0)
        else:
            root.addWidget(bubble, 0)
            root.addStretch(1)
        bubble.setObjectName("bubbleCardUser" if message.role == "user" else "bubbleCardAssistant")

    def update_text(self, text: str) -> None:
        self.message.text = text
        self.body.setText(render_markdown_html(text))

    def minimumSizeHint(self):
        return self.sizeHint()


class ComposerInput(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(False)
        self.setTabChangesFocus(False)


class MainWindow(QMainWindow):
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
        self.prompt_templates = [
            ("代码评审", "请审查当前改动，优先指出 bug、回归风险、边界条件和缺失测试。"),
            ("修复问题", "请先定位根因，再直接修改代码修复问题，并说明验证结果。"),
            ("重构优化", "请在不改变行为的前提下重构这部分实现，提升可读性和可维护性。"),
            ("补测试", "请为当前功能补齐关键测试，覆盖正常路径、边界条件和失败场景。"),
            ("解释代码", "请结合当前仓库上下文解释这段代码的作用、调用链和关键设计点。"),
        ]

        self.setWindowTitle("codex-ui")
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

        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(self.check_account_change)
        self.account_timer.start(3000)

    def setup_shortcuts(self) -> None:
        self.shortcuts: list[QShortcut] = []
        bindings = [
            ("Ctrl+N", self.new_session),
            ("Ctrl+Return", self.send_prompt),
            ("Ctrl+Enter", self.send_prompt),
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

        title = QLabel("codex-ui")
        title.setObjectName("sidebarTitle")
        meta = QLabel("桌面客户端")
        meta.setObjectName("sidebarMeta")
        brand_stack.addWidget(title)
        brand_stack.addWidget(meta)
        brand_row.addWidget(badge, 0, Qt.AlignTop)
        brand_row.addLayout(brand_stack, 1)

        self.work_dir_label = QLabel(str(self.config.work_dir))
        self.work_dir_label.setObjectName("sidebarPath")

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
        layout.addWidget(self.work_dir_label)
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
        top_title_layout.setColumnStretch(1, 1)
        top_title_layout.setColumnStretch(2, 1)
        title = QLabel("Codex 工作台")
        title.setObjectName("pageTitle")
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusText")
        self.status_label.setAlignment(Qt.AlignCenter)
        top_title_layout.addWidget(title, 0, 0, 1, 1, Qt.AlignLeft | Qt.AlignVCenter)
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
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)
        header_row.addWidget(self.header_title, 0)
        header_row.addLayout(session_action_row, 0)
        header_row.addStretch(1)
        header_row.addWidget(self.pin_button, 0)
        header_row.addWidget(self.header_meta, 0)
        header_row.addWidget(self.header_status, 0, Qt.AlignRight)
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
        self.help_label = QLabel("搜索 /  Ctrl+Enter 发送  ·  Ctrl+N 新会话")
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
        if object_name == "inputCard":
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
        font = QFont("Noto Sans CJK SC", 12)
        QApplication.instance().setFont(font)
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
            sessions = [
                s for s in sessions if query in s.thread_name.lower() or query in s.session_id.lower()
            ]
        self.filtered_sessions = sessions
        self.visible_session_limit = self.session_page_size
        self.refresh_session_list()

    def update_session_scope_buttons(self) -> None:
        scope_buttons = {
            "all": self.scope_all_button,
            "pinned": self.scope_pinned_button,
            "recent": self.scope_recent_button,
        }
        for scope, button in scope_buttons.items():
            selected = scope == self.session_scope
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)

    def refresh_session_list(self) -> None:
        visible_sessions = self.filtered_sessions[: self.visible_session_limit]
        query = self.search.text().strip()
        self.session_list.blockSignals(True)
        self.session_list.clear()
        current_row = -1

        pinned_sessions = [s for s in visible_sessions if s.session_id in self.pinned_session_ids]
        regular_sessions = [s for s in visible_sessions if s.session_id not in self.pinned_session_ids]

        def add_group(title: str, group_sessions: list[SessionSummary], time_grouped: bool) -> None:
            nonlocal current_row
            previous_group = ""
            if not group_sessions:
                return
            if not time_grouped:
                header_item = QListWidgetItem()
                header_item.setFlags(Qt.NoItemFlags)
                header_widget = SessionGroupHeader(title)
                header_item.setSizeHint(header_widget.sizeHint())
                self.session_list.addItem(header_item)
                self.session_list.setItemWidget(header_item, header_widget)
            for session in group_sessions:
                if time_grouped:
                    group = session_group_label(session.updated_at_raw)
                    if group != previous_group:
                        header_item = QListWidgetItem()
                        header_item.setFlags(Qt.NoItemFlags)
                        header_widget = SessionGroupHeader(group)
                        header_item.setSizeHint(header_widget.sizeHint())
                        self.session_list.addItem(header_item)
                        self.session_list.setItemWidget(header_item, header_widget)
                        previous_group = group

                item = QListWidgetItem()
                item.setData(Qt.UserRole, session.session_id)
                preview_widget = SessionListItem(session, False, query)
                item.setSizeHint(preview_widget.sizeHint())
                self.session_list.addItem(item)
                selected = session.session_id == self.active_session_id
                self.session_list.setItemWidget(item, SessionListItem(session, selected, query))
                if selected:
                    current_row = self.session_list.row(item)

        add_group("置顶", pinned_sessions, time_grouped=False)
        add_group("", regular_sessions, time_grouped=True)

        if current_row >= 0:
            self.session_list.setCurrentRow(current_row)
        elif visible_sessions:
            for i in range(self.session_list.count()):
                item = self.session_list.item(i)
                if item and item.data(Qt.UserRole):
                    self.session_list.setCurrentRow(i)
                    break
        elif self.active_session_id is not None:
            self.active_session_id = None
        self.session_list.blockSignals(False)
        self.load_more_button.setVisible(len(self.filtered_sessions) > len(visible_sessions))

    def refresh_sessions_for_account(self, keep_selection: bool = True) -> None:
        previous = self.active_session_id if keep_selection else None
        self.sessions = load_sessions(self.config, session_aliases=self.session_aliases)
        visible_ids = {session.session_id for session in self.sessions}
        self.active_session_id = previous if previous in visible_ids else (self.sessions[0].session_id if self.sessions else None)
        self.apply_session_filters()
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
        self.copy_resume_button.setEnabled(has_session)
        self.rename_session_button.setEnabled(has_session)
        self.copy_session_id_button.setEnabled(has_session)
        self.open_session_file_button.setEnabled(has_session and bool(find_session_file(self.config.codex_home, self.active_session_id)))
        self.clear_session_alias_button.setEnabled(has_session and self.active_session_id in self.session_aliases)
        self.permission_combo.setEnabled(True)

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
        current_title = next(
            (session.thread_name for session in self.sessions if session.session_id == self.active_session_id),
            self.active_session_id[:8],
        )
        new_title, accepted = QInputDialog.getText(self, "重命名会话", "本地别名", text=current_title)
        if not accepted:
            return
        alias = new_title.strip()
        if not alias:
            self.clear_current_session_alias()
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

    def clear_layout_widgets(self, layout: QVBoxLayout | QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                self.clear_layout_widgets(child_layout)

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

    def refresh_account_panel(self) -> None:
        self.account_manage_button.setEnabled(self.account_worker is None)
        self.account_manage_button.setText("账号处理中..." if self.account_worker is not None else "账号管理")
        if self.account_dialog is not None and self.account_dialog.isVisible():
            self.account_dialog.reload()

    def sync_account_state(self, keep_selection: bool = True, refresh_sessions: bool = False) -> None:
        self.active_account = load_active_account(self.config)
        self.all_accounts = load_all_accounts(self.config)
        self.session_account_map = seed_session_account_map(
            self.config, load_session_account_map(self.config)
        )
        self.update_account_label()
        self.refresh_account_panel()
        if refresh_sessions:
            self.refresh_sessions_for_account(keep_selection=keep_selection)

    def switch_account(self, account_key: str, label: str) -> None:
        if self.account_worker is not None or self.has_running_worker():
            return
        if not switch_active_account_local(self.config.codex_home, account_key):
            QMessageBox.critical(self, "codex-ui", "未找到目标账号，无法切换。")
            return
        self.sync_account_state(keep_selection=True, refresh_sessions=False)
        self.set_status("", "idle")

    def login_new_account(self) -> None:
        if self.account_worker is not None:
            return
        if not (self.codex_auth_path and (Path(self.codex_auth_path).exists() or shutil.which(Path(self.codex_auth_path).name))):
            QMessageBox.critical(self, "codex-ui", "未找到 codex-auth，无法发起新账号登录。")
            return
        self.set_status("正在打开账号登录流程...", "idle")
        self.account_worker = AccountActionWorker(
            self.config,
            self.codex_auth_path,
            ["login"],
            "新账号已登录",
        )
        self.account_worker.finished_ok.connect(self.on_account_action_finished)
        self.account_worker.failed.connect(self.on_account_action_failed)
        self.account_worker.finished.connect(self.on_account_worker_thread_finished)
        self.refresh_account_panel()
        self.account_worker.start()

    def reload_accounts(self) -> None:
        self.sync_account_state(keep_selection=True, refresh_sessions=False)
        self.set_status("", "idle")

    def refresh_account_usage(self) -> None:
        if self.account_worker is not None:
            return
        if not (self.codex_auth_path and (Path(self.codex_auth_path).exists() or shutil.which(Path(self.codex_auth_path).name))):
            QMessageBox.critical(self, "codex-ui", "未找到 codex-auth，无法刷新账号用量。")
            return
        self.account_action_restore_key = self.active_account.account_key if self.active_account else ""
        self.account_worker = AccountActionWorker(
            self.config,
            self.codex_auth_path,
            ["list"],
            "账号用量已刷新",
        )
        self.account_worker.finished_ok.connect(self.on_account_action_finished)
        self.account_worker.failed.connect(self.on_account_action_failed)
        self.account_worker.finished.connect(self.on_account_worker_thread_finished)
        self.refresh_account_panel()
        self.account_worker.start()

    def open_account_dialog(self) -> None:
        self.reload_accounts()
        dialog = AccountDialog(self)
        self.account_dialog = dialog
        dialog.exec()
        self.account_dialog = None

    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self)
        dialog.exec()

    def on_account_action_finished(self, message: str) -> None:
        if self.account_action_restore_key:
            switch_active_account_local(self.config.codex_home, self.account_action_restore_key)
            self.account_action_restore_key = ""
        self.sync_account_state(keep_selection=True, refresh_sessions=False)
        if message == "新账号已登录":
            self.set_status(message, "idle")
        else:
            self.set_status("", "idle")

    def on_account_action_failed(self, error: str) -> None:
        if self.account_action_restore_key:
            switch_active_account_local(self.config.codex_home, self.account_action_restore_key)
            self.account_action_restore_key = ""
            self.sync_account_state(keep_selection=True, refresh_sessions=False)
        self.refresh_account_panel()
        compact_error = " ".join((error or "账号操作失败").split()).strip()
        self.set_status("账号操作失败", "idle")
        QMessageBox.critical(self, "codex-ui", compact_error)

    def on_account_worker_thread_finished(self) -> None:
        worker = self.sender()
        if worker is self.account_worker:
            self.account_worker.deleteLater()
            self.account_worker = None
            self.refresh_account_panel()

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
        if self.request_state_label.text() == "处理中...":
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

    def retry_last_prompt(self) -> None:
        if (not self.last_prompt and not self.last_attachments) or self.is_current_session_busy():
            return
        self.input_box.setPlainText(self.last_prompt)
        self.pending_attachments = [AttachmentInfo(path=item.path, kind=item.kind) for item in self.last_attachments]
        self.refresh_attachment_widgets()
        self.send_prompt()

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

    def update_account_label(self) -> None:
        if not self.active_account:
            self.account_label.setText("账号未识别")
            return
        if self.active_account.email:
            self.account_label.setText(f"当前账号 · {self.active_account.email}")
        else:
            self.account_label.setText(f"当前账号 · {self.active_account.display_name}")

    def current_local_account(self) -> LocalAccountInfo | None:
        if not self.active_account:
            return None
        for account in self.all_accounts:
            if account.account_key == self.active_account.account_key:
                return account
        return None

    def apply_runtime_config(self, input_method_changed: bool = False) -> None:
        self.codex_auth_path = resolve_codex_auth_path(self.config)
        self.work_dir_label.setText(str(self.config.work_dir))
        self.update_permission_selector()
        self.refresh_account_panel()
        if input_method_changed:
            self.set_status("设置已保存，输入法策略需重启应用后完全生效", "idle")
        else:
            self.set_status("设置已保存", "idle")

    def copy_current_account_info(self) -> None:
        account = self.current_local_account()
        if not account:
            QMessageBox.critical(self, "codex-ui", "当前没有可复制的账号信息。")
            return
        parts = [f"账号: {account.display_name}"]
        if account.email and account.email != account.display_name:
            parts.append(f"邮箱: {account.email}")
        if account.plan:
            parts.append(f"套餐: {account.plan}")
        if account.auth_mode:
            parts.append(f"认证: {account.auth_mode}")
        parts.append(f"Account Key: {account.account_key}")
        if account.usage_detail:
            parts.append(f"用量: {account.usage_detail}")
        QGuiApplication.clipboard().setText("\n".join(parts))
        self.set_status("已复制当前账号信息", "idle")

    def open_accounts_directory(self) -> None:
        accounts_dir = self.config.codex_home / "accounts"
        if not accounts_dir.exists():
            QMessageBox.critical(self, "codex-ui", f"账号目录不存在：{accounts_dir}")
            return
        opener = shutil.which("xdg-open")
        if not opener:
            QGuiApplication.clipboard().setText(str(accounts_dir))
            QMessageBox.information(self, "codex-ui", f"未找到 xdg-open，已复制路径：\n{accounts_dir}")
            return
        try:
            subprocess.Popen(
                [opener, str(accounts_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            QMessageBox.critical(self, "codex-ui", str(exc))
            return
        self.set_status("", "idle")

    def bind_session_to_active_account(self, session_id: str | None) -> None:
        if not session_id or not self.active_account:
            return
        if self.session_account_map.get(session_id) == self.active_account.account_key:
            return
        self.session_account_map[session_id] = self.active_account.account_key
        save_session_account_map(self.session_account_map)

    def remember_request_account(self) -> None:
        self.request_account_key = self.active_account.account_key if self.active_account else ""

    def restore_request_account(self) -> None:
        account_key = self.request_account_key
        self.request_account_key = ""
        if not account_key:
            return
        latest = load_active_account(self.config)
        latest_key = latest.account_key if latest else ""
        if latest_key != account_key:
            switch_active_account_local(self.config.codex_home, account_key)
        self.sync_account_state(keep_selection=True, refresh_sessions=False)

    def mark_session_updated(self, session_id: str | None, raw_time: str | None = None) -> None:
        if not session_id:
            return
        latest_raw = raw_time or current_local_iso()
        latest_label = to_local_time(latest_raw, "%m-%d %H:%M")
        for collection in (self.sessions, self.filtered_sessions):
            for session in collection:
                if session.session_id != session_id:
                    continue
                session.updated_at_raw = latest_raw
                session.updated_at = latest_label

    def check_account_change(self) -> None:
        if self.has_running_worker():
            return
        latest = load_active_account(self.config)
        latest_key = latest.account_key if latest else ""
        current_key = self.active_account.account_key if self.active_account else ""
        if latest_key == current_key:
            return
        self.sync_account_state(keep_selection=False, refresh_sessions=False)
        self.set_status("", "idle")

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

    def set_status(self, text: str, chip: str | None = None) -> None:
        compact = (text or "").strip()
        self.status_label.setText(compact)
        tone = "failure" if any(token in compact for token in ("失败", "错误")) else "success"
        self.status_label.setProperty("tone", tone if compact else "")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        if chip:
            self.header_status.setText(chip)

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
        self.set_status("", "idle")
        QMessageBox.critical(self, "codex-ui", error or "未知错误")

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

    def closeEvent(self, event: QCloseEvent) -> None:
        for worker in list(self.workers.values()):
            if worker.isRunning():
                worker.stop()
                worker.wait(3000)
        if self.account_worker is not None and self.account_worker.isRunning():
            self.account_worker.stop()
            self.account_worker.wait(3000)
        event.accept()


def main() -> None:
    config = load_config()
    setup_qt_input_method_env(config.input_method_strategy)
    app = QApplication(sys.argv)
    icon = load_app_icon()
    if icon is not None and not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow(config)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
