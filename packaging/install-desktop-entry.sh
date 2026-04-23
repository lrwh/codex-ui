#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/codex-ui.desktop.template"
TARGET_DIR="${HOME}/.local/share/applications"
TARGET_FILE="${TARGET_DIR}/codex-ui.desktop"
ICON_SOURCE="${SCRIPT_DIR}/codex-ui.svg"
ICON_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
ICON_TARGET="${ICON_DIR}/codex-ui.svg"

mkdir -p "$TARGET_DIR"
mkdir -p "$ICON_DIR"
install -m 0644 "$ICON_SOURCE" "$ICON_TARGET"
sed "s|__APPDIR__|$SCRIPT_DIR|g" "$TEMPLATE" > "$TARGET_FILE"
chmod +x "$SCRIPT_DIR/run-codex-ui.sh"

printf '桌面入口已安装到 %s\n' "$TARGET_FILE"
printf '应用图标已安装到 %s\n' "$ICON_TARGET"
