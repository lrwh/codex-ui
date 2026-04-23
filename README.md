# codex-ui

`codex-ui` 是一个非浏览器形态的 Codex 桌面客户端，基于本机已安装的 `codex` / `codex-auth` 工作，直接读取本地 `~/.codex` 数据，提供会话浏览、续聊、多账号管理、附件输入和桌面打包能力。

![codex-ui 预览](./codex.png)

## 功能特性

- 桌面客户端：不依赖浏览器、不依赖 WebView，使用 PySide6 构建原生窗口。
- 多会话：合并读取 `~/.codex/session_index.jsonl` 与 `~/.codex/sessions/**/*.jsonl`。
- 会话续聊：新会话走 `codex exec --json`，已有会话走 `codex exec resume --json`。
- 多账号：读取 `~/.codex/accounts/registry.json`，支持账号管理、切换和用量刷新。
- 长会话优化：默认渲染最近消息，支持按批加载更早内容。
- 附件输入：提示词支持图片、`.log`、`.md`、`.markdown` 附件。
- 本地设置：支持工作目录、权限模式、Codex 路径和输入法策略配置；模型和推理强度通过输入框 `/model` 切换。
- 桌面发布：支持 PyInstaller 打包，并可安装 `.desktop` 启动入口和应用图标。

## 目录结构

```text
.
├── desktop_app.py                  # PySide6 桌面客户端入口
├── desktop_app_core.py             # 配置、数据模型和会话/账号数据读取
├── desktop_app_workers.py          # 后台任务线程
├── desktop_app_ui.py               # 对话框和复用 UI 组件
├── desktop_app_window*.py          # 主窗口和功能 mixin
├── capture_desktop.py              # 离屏截图辅助脚本
├── codex-ui.spec                   # PyInstaller 配置
├── packaging/                      # 桌面入口、图标和启动脚本
├── scripts/package_desktop.sh      # 桌面版打包脚本
├── README.md
└── TASKS.md
```

## 预置要求

运行前需要本机已经具备：

- Python 3.10+
- PySide6
- 已登录并可用的 `codex` CLI
- 可选：`codex-auth`，用于账号登录、账号切换和用量刷新

如果还没有安装 Python 依赖，可以使用：

```bash
python3 -m pip install PySide6 PyInstaller
```

## 直接运行

```bash
cd /home/liurui/code/codex-ui
python3 desktop_app.py
```

首次启动会自动生成本地配置：

```text
~/.config/codex-ui/config.json
```

## 桌面版打包

```bash
cd /home/liurui/code/codex-ui
bash scripts/package_desktop.sh
```

打包完成后会生成：

```text
release/codex-ui-linux-x86_64/
release/codex-ui-linux-x86_64.tar.gz
```

解压后的用户可以直接运行：

```bash
cd codex-ui-linux-x86_64
./run-codex-ui.sh
```

安装桌面启动入口：

```bash
cd codex-ui-linux-x86_64
./install-desktop-entry.sh
```

安装脚本会写入：

- `~/.local/share/applications/codex-ui.desktop`
- `~/.local/share/icons/hicolor/scalable/apps/codex-ui.svg`

## 快捷键

- `Ctrl+Enter` / `Ctrl+Return`：发送当前输入
- `Esc`：停止当前正在处理的请求，也可点击输入区右下角“停止”
- `Ctrl+N`：新建会话
- `/model`：在输入框中打开模型和推理强度选择；也可用 `/model gpt-5.4 high` 直接切换
- `/`：聚焦会话搜索
- `PgUp` / `PgDown`：会话或消息区域滚动
- `Home` / `End`：跳到会话列表首尾

## 配置说明

默认配置文件：

```text
~/.config/codex-ui/config.json
```

示例：

```json
{
  "codex_path": "codex",
  "codex_home": "~/.codex",
  "work_dir": "/home/liurui/code",
  "model": "",
  "model_reasoning_effort": "",
  "approval_policy": "on-request",
  "sandbox_mode": "workspace-write",
  "skip_git_repo_check": true,
  "recent_session_limit": 30,
  "input_method_strategy": "auto"
}
```

桌面设置面板里推荐直接使用“权限模式”，它会自动映射到底层的 `approval_policy` 和 `sandbox_mode`。

输入法策略：

- `auto`：默认策略，优先跟随当前桌面环境，必要时自动兼容 `fcitx / ibus / xim`。
- `system`：完全不改输入法环境变量，直接使用当前系统配置。
- `fcitx`：强制使用 `fcitx`。
- `ibus`：强制使用 `ibus`，并自动尝试启动 `ibus-daemon`。
- `xim`：强制使用 Qt 的 `xim` 回退模式。

## 注意事项

- `build/`、`dist/`、`release/` 属于本地构建产物，默认不会入库。
- `codex.png` 是 README 预览截图，会随源码一起提交。
- 多账号状态依赖本地 `~/.codex/accounts/registry.json`，刷新用量时会保持当前 UI 选中的账号不变。
