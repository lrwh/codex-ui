#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_DIR="${ROOT_DIR}/release"
ARCHIVE_NAME="codex-ui-linux-x86_64"
STAGE_DIR="${RELEASE_DIR}/${ARCHIVE_NAME}"
DEB_ROOT="${RELEASE_DIR}/deb-root"
PKG_NAME="codex-ui"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-$(dpkg --print-architecture)}"
INSTALL_PREFIX="/opt/${PKG_NAME}"
OUTPUT_DEB="${RELEASE_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"

cd "$ROOT_DIR"

bash "$ROOT_DIR/scripts/package_desktop.sh"

rm -rf "$DEB_ROOT"
mkdir -p \
  "$DEB_ROOT/DEBIAN" \
  "$DEB_ROOT${INSTALL_PREFIX}" \
  "$DEB_ROOT/usr/share/applications" \
  "$DEB_ROOT/usr/share/icons/hicolor/scalable/apps"

cp -r "$STAGE_DIR/$PKG_NAME" "$DEB_ROOT${INSTALL_PREFIX}/"
cp "$STAGE_DIR/run-codex-ui.sh" "$DEB_ROOT${INSTALL_PREFIX}/"
cp "$ROOT_DIR/packaging/codex-ui.svg" \
  "$DEB_ROOT/usr/share/icons/hicolor/scalable/apps/codex-ui.svg"

sed "s#__APPDIR__#${INSTALL_PREFIX}#g" \
  "$ROOT_DIR/packaging/codex-ui.desktop.template" \
  > "$DEB_ROOT/usr/share/applications/codex-ui.desktop"

chmod 755 \
  "$DEB_ROOT${INSTALL_PREFIX}/run-codex-ui.sh" \
  "$DEB_ROOT${INSTALL_PREFIX}/${PKG_NAME}/codex-ui"

INSTALLED_SIZE="$(du -sk "$DEB_ROOT" | awk '{print $1}')"

cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: liurui <liurui_wuhan@163.com>
Installed-Size: ${INSTALLED_SIZE}
Description: Codex for Linux desktop client
 Codex for Linux 是一个面向本地 Codex CLI 的 Linux 桌面客户端，
 提供会话管理、账号切换、附件输入与打包发布能力。
EOF

dpkg-deb --build "$DEB_ROOT" "$OUTPUT_DEB"

printf 'Deb 打包完成：\n'
printf '  安装包: %s\n' "$OUTPUT_DEB"
