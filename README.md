# codex-ui

一个纯终端形态的 Codex 本地客户端原型，不依赖浏览器，也不依赖 WebView。

## 设计目标

- 非浏览器：直接运行在终端，适合当前操作系统不支持官方客户端的场景
- 多会话：读取 `~/.codex/session_index.jsonl` 和 `~/.codex/sessions/*` 展示历史
- 多账号：读取 `~/.codex/accounts/registry.json` 展示本地账号，并支持切换当前活跃账号
- 可续聊：新会话走 `codex exec --json`，已有会话走 `codex exec resume --json`
- 本地优先：使用本机已安装的 `codex-cli`
- 可配置：首次启动会自动生成 `~/.config/codex-ui/config.json`
- 长会话优化：默认优先渲染最近一批消息，并支持按批加载更早内容

## 界面布局

- 左侧：历史会话列表
- 左侧上方：当前账号、本地账号面板与登录/切换入口
- 右上：当前会话标题
- 中间：消息流
- 右下：输入区、运行状态、token 信息和快捷键

## 快捷键

- `Tab`：切换会话栏 / 输入框焦点
- `/`：搜索过滤会话
- `Ctrl+S`：发送当前输入
- `Ctrl+N`：新建会话
- `Ctrl+R`：刷新会话与消息
- `Ctrl+,`：打开设置面板
- `PgUp` / `PgDown`：在会话栏翻页，或在消息区半屏滚动
- `Home` / `End`：跳到会话列表首尾
- `Q`：退出

## 运行

桌面版：

```bash
cd /home/liurui/code/codex-ui
python3 desktop_app.py
```

打包桌面版：

```bash
cd /home/liurui/code/codex-ui
bash scripts/package_desktop.sh
```

打包完成后会生成：

- `release/codex-ui-linux-x86_64/`
- `release/codex-ui-linux-x86_64.tar.gz`

用户解压后可直接运行：

```bash
cd codex-ui-linux-x86_64
./run-codex-ui.sh
```

如果要在桌面环境里增加启动入口：

```bash
cd codex-ui-linux-x86_64
./install-desktop-entry.sh
```

安装脚本会同时完成两件事：

- 安装 `~/.local/share/applications/codex-ui.desktop`
- 安装 `~/.local/share/icons/hicolor/scalable/apps/codex-ui.svg`

终端版：

```bash
cd /home/liurui/code/codex-ui
go mod tidy
go run ./cmd/codex-ui
```

终端版可选参数：

```bash
go run ./cmd/codex-ui --cwd /home/liurui/code --model gpt-5.4
```

## 配置文件

默认路径：

```text
~/.config/codex-ui/config.json
```

示例：

```json
{
  "codex_path": "/home/liurui/.nvm/versions/node/v24.1.0/bin/codex",
  "codex_home": "/home/liurui/.codex",
  "work_dir": "/home/liurui/code",
  "model": "",
  "approval_policy": "on-request",
  "sandbox_mode": "workspace-write",
  "skip_git_repo_check": true,
  "recent_session_limit": 30,
  "input_method_strategy": "auto"
}
```

说明：

- 桌面版设置面板里推荐直接使用“权限模式”，它会自动映射到底层的 `approval_policy` 和 `sandbox_mode`
- `full_auto` 仍作为兼容字段保留在代码里，但不再作为用户主配置入口

输入法相关配置：

- `input_method_strategy: "auto"`：默认模式。优先跟随当前环境，必要时在 `fcitx / ibus / xim` 之间自动兼容。
- `input_method_strategy: "system"`：完全不改输入法环境变量，直接使用你当前桌面会话的配置。
- `input_method_strategy: "fcitx"`：强制使用 `fcitx`。
- `input_method_strategy: "ibus"`：强制使用 `ibus`，并自动尝试启动 `ibus-daemon`。
- `input_method_strategy: "xim"`：强制使用 Qt 的 `xim` 回退模式。

## 当前范围

这个版本是可运行的终端客户端原型，重点解决：

- 没有官方 GUI 客户端时的本地使用问题
- 会话查看与续聊
- 统一输入、状态和历史入口
- 会话关键字过滤
- 本地设置保存

后续如果要继续增强，建议优先补：

- 流式 token 增量展示
- 会话搜索和重命名
- 本地快捷命令面板
- 任务状态侧边栏
