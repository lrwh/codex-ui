#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="codex-ui"
ARCHIVE_NAME="${APP_NAME}-linux-x86_64"
BUILD_DIR="${ROOT_DIR}/build"
DIST_DIR="${ROOT_DIR}/dist"
RELEASE_DIR="${ROOT_DIR}/release"
STAGE_DIR="${RELEASE_DIR}/${ARCHIVE_NAME}"

cd "$ROOT_DIR"

rm -rf "$BUILD_DIR" "$DIST_DIR" "$STAGE_DIR"
mkdir -p "$STAGE_DIR"

python3 -m PyInstaller --noconfirm "$ROOT_DIR/${APP_NAME}.spec"

cp -r "$DIST_DIR/$APP_NAME" "$STAGE_DIR/"
cp "$ROOT_DIR/packaging/run-codex-ui.sh" "$STAGE_DIR/"
cp "$ROOT_DIR/packaging/install-desktop-entry.sh" "$STAGE_DIR/"
cp "$ROOT_DIR/packaging/codex-ui.desktop.template" "$STAGE_DIR/"
cp "$ROOT_DIR/packaging/codex-ui.svg" "$STAGE_DIR/"
cp "$ROOT_DIR/README.md" "$STAGE_DIR/README.md"

chmod +x "$STAGE_DIR/run-codex-ui.sh" "$STAGE_DIR/install-desktop-entry.sh"

tar -C "$RELEASE_DIR" -czf "$RELEASE_DIR/${ARCHIVE_NAME}.tar.gz" "$ARCHIVE_NAME"

printf '打包完成：\n'
printf '  目录包: %s\n' "$STAGE_DIR"
printf '  压缩包: %s\n' "$RELEASE_DIR/${ARCHIVE_NAME}.tar.gz"
