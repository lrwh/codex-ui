# Build Guide

本文档记录 `Codex for Linux` 的构建与打包方式。

## 依赖

```bash
python3 -m pip install PySide6 PyInstaller
```

此外需要本机已安装并可用：

- `codex`
- 可选：`codex-auth`
- `dpkg-deb`，用于构建 `.deb` 安装包

## 构建桌面目录包与 tar.gz

```bash
cd /home/liurui/code/codex-ui
bash scripts/package_desktop.sh
```

产物：

```text
release/codex-ui-linux-x86_64/
release/codex-ui-linux-x86_64.tar.gz
```

## 构建 `.deb`

```bash
cd /home/liurui/code/codex-ui
bash scripts/package_deb.sh
```

默认产物：

```text
release/codex-ui_0.1.0_amd64.deb
```

如需覆盖版本号：

```bash
VERSION=0.1.1 bash scripts/package_deb.sh
```

## 构建结果验证

查看 `.deb` 控制信息：

```bash
dpkg-deb -I release/codex-ui_0.1.0_amd64.deb
```

查看包内文件：

```bash
dpkg-deb -c release/codex-ui_0.1.0_amd64.deb
```

离屏启动验证：

```bash
QT_QPA_PLATFORM=offscreen timeout 3s release/codex-ui-linux-x86_64/run-codex-ui.sh
```

## 发布文件

当前发布链路包含两类交付物：

- `.deb` 安装包
- `tar.gz` 便携目录包
