package app

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"codex-ui/internal/codexui"
	"codex-ui/internal/storage"

	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

type focusArea int

const (
	focusSessions focusArea = iota
	focusInput
	focusFilter
)

type sessionsLoadedMsg struct {
	sessions []storage.SessionSummary
	err      error
}

type conversationLoadedMsg struct {
	sessionID string
	messages  []storage.ChatMessage
	err       error
}

type codexEventMsg struct {
	event codexui.Event
	ok    bool
}

type streamTickMsg struct{}

type model struct {
	config     storage.AppConfig
	configPath string
	store      *storage.SessionStore
	client     *codexui.Client
	viewport   viewport.Model
	input      textarea.Model
	filter     textinput.Model

	width  int
	height int

	focus             focusArea
	sessions          []storage.SessionSummary
	filteredSessions  []storage.SessionSummary
	selected          int
	sessionOffset     int
	activeSessionID   string
	activeSessionName string
	messages          []storage.ChatMessage
	usage             codexui.TokenUsage
	status            string
	running           bool
	lastAssistant     string
	cancelRun         context.CancelFunc
	runEvents         <-chan codexui.Event
	streaming         bool
	streamTarget      string
	streamVisible     string
	streamMessageIdx  int
	pendingReload     bool

	settingsOpen     bool
	settingsFocus    int
	settingsModel    textinput.Model
	settingsWork     textinput.Model
	settingsLimit    textinput.Model
	settingsFullAuto bool
}

func NewModel(cfg storage.AppConfig, configPath string) tea.Model {
	ta := textarea.New()
	ta.Placeholder = "输入提示词，Ctrl+S 发送，Tab 切换焦点，Ctrl+N 新会话"
	ta.Focus()
	ta.CharLimit = 0
	ta.SetWidth(80)
	ta.SetHeight(5)
	ta.ShowLineNumbers = false

	filter := textinput.New()
	filter.Placeholder = "按 / 搜索会话"
	filter.CharLimit = 120
	filter.Width = 24

	settingsModel := textinput.New()
	settingsModel.Placeholder = "例如 gpt-5.4"
	settingsModel.SetValue(cfg.Model)

	settingsWork := textinput.New()
	settingsWork.Placeholder = "工作目录"
	settingsWork.SetValue(cfg.WorkDir)

	settingsLimit := textinput.New()
	settingsLimit.Placeholder = "最近会话数量"
	settingsLimit.SetValue(strconv.Itoa(cfg.RecentSessionLimit))

	vp := viewport.New(80, 20)
	vp.SetContent("正在加载会话...")

	return &model{
		config:           cfg,
		configPath:       configPath,
		store:            storage.NewSessionStore(cfg.CodexHome),
		client:           codexui.NewClient(cfg),
		viewport:         vp,
		input:            ta,
		filter:           filter,
		focus:            focusInput,
		status:           "准备就绪",
		settingsModel:    settingsModel,
		settingsWork:     settingsWork,
		settingsLimit:    settingsLimit,
		settingsFullAuto: cfg.FullAuto,
		streamMessageIdx: -1,
	}
}

func (m *model) Init() tea.Cmd {
	return tea.Batch(m.loadSessionsCmd(), textarea.Blink)
}

func (m *model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.resize()
		m.clampSessionWindow()
		m.refreshViewport()
		return m, nil
	case sessionsLoadedMsg:
		if msg.err != nil {
			m.status = "加载会话失败: " + msg.err.Error()
			return m, nil
		}
		m.sessions = msg.sessions
		m.applyFilter()
		if m.selected >= len(m.filteredSessions) && len(m.filteredSessions) > 0 {
			m.selected = len(m.filteredSessions) - 1
		}
		m.clampSessionWindow()
		if m.activeSessionID == "" && len(m.filteredSessions) > 0 {
			m.activeSessionID = m.filteredSessions[0].ID
			m.activeSessionName = m.filteredSessions[0].ThreadName
			return m, m.loadConversationCmd(m.activeSessionID)
		}
		m.status = fmt.Sprintf("已加载 %d 个会话，筛出 %d 个", len(m.sessions), len(m.filteredSessions))
		return m, nil
	case conversationLoadedMsg:
		if msg.err != nil {
			m.status = "加载消息失败: " + msg.err.Error()
			return m, nil
		}
		m.streamMessageIdx = -1
		m.streaming = false
		m.streamTarget = ""
		m.streamVisible = ""
		m.pendingReload = false
		m.activeSessionID = msg.sessionID
		m.activeSessionName = m.sessionNameByID(msg.sessionID)
		m.messages = msg.messages
		m.refreshViewport()
		if strings.TrimSpace(msg.sessionID) == "" {
			m.status = "新会话已就绪"
		} else {
			m.status = fmt.Sprintf("会话 %s 已就绪", shortID(msg.sessionID))
		}
		return m, nil
	case codexEventMsg:
		if !msg.ok {
			return m, nil
		}
		switch msg.event.Type {
		case codexui.EventThreadStarted:
			if strings.TrimSpace(msg.event.ThreadID) != "" {
				m.activeSessionID = msg.event.ThreadID
				m.activeSessionName = m.sessionNameByID(msg.event.ThreadID)
			}
			m.status = "Codex 正在处理..."
		case codexui.EventAgentMessage:
			return m, m.startAssistantStream(msg.event.Message)
		case codexui.EventUsage:
			m.usage = msg.event.Usage
		case codexui.EventError:
			m.running = false
			m.cancelCurrentRun()
			m.stopAssistantStream(false)
			m.status = "请求失败: " + msg.event.Err.Error()
			return m, nil
		case codexui.EventDone:
			m.running = false
			m.cancelCurrentRun()
			m.status = "本轮完成"
			if m.streaming {
				m.pendingReload = true
				return m, nil
			}
			return m, tea.Batch(m.loadSessionsCmd(), m.loadConversationCmd(m.activeSessionID))
		}
		return m, m.listenCmd(m.runEvents)
	case streamTickMsg:
		return m, m.advanceAssistantStream()
	case tea.KeyMsg:
		if m.settingsOpen {
			return m.updateSettings(msg)
		}

		switch msg.String() {
		case "ctrl+c", "q":
			m.cancelCurrentRun()
			return m, tea.Quit
		case "tab":
			m.toggleFocus()
			return m, nil
		case "/":
			if !m.running {
				m.focus = focusFilter
				m.input.Blur()
				m.filter.Focus()
				m.status = "输入关键字过滤会话，Esc 退出过滤"
			}
			return m, nil
		case "esc":
			if m.focus == focusFilter {
				m.focus = focusSessions
				m.filter.Blur()
				m.status = "已退出会话过滤"
				return m, nil
			}
		case "ctrl+n":
			if m.running {
				return m, nil
			}
			m.activeSessionID = ""
			m.activeSessionName = "新会话"
			m.messages = nil
			m.usage = codexui.TokenUsage{}
			m.lastAssistant = ""
			m.stopAssistantStream(true)
			m.status = "已切换到新会话"
			m.refreshViewport()
			return m, nil
		case "ctrl+r":
			return m, tea.Batch(m.loadSessionsCmd(), m.loadConversationCmd(m.activeSessionID))
		case "ctrl+f":
			m.config.FullAuto = !m.config.FullAuto
			m.client = codexui.NewClient(m.config)
			if m.config.FullAuto {
				m.status = "已开启 Full Auto"
			} else {
				m.status = "已关闭 Full Auto"
			}
			return m, nil
		case "ctrl+,":
			if m.running {
				m.status = "请等待当前请求完成"
				return m, nil
			}
			m.openSettings()
			return m, nil
		case "ctrl+s":
			if m.focus == focusInput {
				return m, m.sendPrompt()
			}
		case "up", "k":
			if m.focus == focusSessions || m.focus == focusFilter {
				if m.running {
					m.status = "请等待当前请求完成"
					return m, nil
				}
				if m.selected > 0 {
					m.selected--
					m.clampSessionWindow()
					return m, m.loadConversationCmd(m.filteredSessions[m.selected].ID)
				}
				return m, nil
			}
			m.viewport.LineUp(1)
			return m, nil
		case "down", "j":
			if m.focus == focusSessions || m.focus == focusFilter {
				if m.running {
					m.status = "请等待当前请求完成"
					return m, nil
				}
				if m.selected < len(m.filteredSessions)-1 {
					m.selected++
					m.clampSessionWindow()
					return m, m.loadConversationCmd(m.filteredSessions[m.selected].ID)
				}
				return m, nil
			}
			m.viewport.LineDown(1)
			return m, nil
		case "pgup":
			if m.focus == focusSessions || m.focus == focusFilter {
				if m.running {
					m.status = "请等待当前请求完成"
					return m, nil
				}
				step := m.visibleSessionCount()
				if step < 1 {
					step = 1
				}
				m.selected = max(0, m.selected-step)
				m.clampSessionWindow()
				if id := m.selectedSessionID(); id != "" {
					return m, m.loadConversationCmd(id)
				}
				return m, nil
			}
			m.viewport.HalfViewUp()
			return m, nil
		case "pgdown":
			if m.focus == focusSessions || m.focus == focusFilter {
				if m.running {
					m.status = "请等待当前请求完成"
					return m, nil
				}
				step := m.visibleSessionCount()
				if step < 1 {
					step = 1
				}
				m.selected = min(len(m.filteredSessions)-1, m.selected+step)
				m.clampSessionWindow()
				if id := m.selectedSessionID(); id != "" {
					return m, m.loadConversationCmd(id)
				}
				return m, nil
			}
			m.viewport.HalfViewDown()
			return m, nil
		case "home":
			if m.focus == focusSessions || m.focus == focusFilter {
				if len(m.filteredSessions) == 0 {
					return m, nil
				}
				m.selected = 0
				m.clampSessionWindow()
				return m, m.loadConversationCmd(m.filteredSessions[m.selected].ID)
			}
		case "end":
			if m.focus == focusSessions || m.focus == focusFilter {
				if len(m.filteredSessions) == 0 {
					return m, nil
				}
				m.selected = len(m.filteredSessions) - 1
				m.clampSessionWindow()
				return m, m.loadConversationCmd(m.filteredSessions[m.selected].ID)
			}
		}
	}

	if m.focus == focusFilter {
		var cmd tea.Cmd
		m.filter, cmd = m.filter.Update(msg)
		m.applyFilter()
		if len(m.filteredSessions) == 0 {
			m.selected = 0
		} else if m.selected >= len(m.filteredSessions) {
			m.selected = len(m.filteredSessions) - 1
		}
		m.clampSessionWindow()
		return m, cmd
	}

	if m.focus == focusInput {
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		return m, cmd
	}

	return m, nil
}

func (m *model) View() string {
	if m.width == 0 || m.height == 0 {
		return "正在初始化界面..."
	}

	styles := newStyles(m.width)
	leftWidth := styles.LeftPanelWidth
	rightWidth := max(24, m.width-leftWidth-1)
	contentWidth := min(rightWidth-1, 92)

	left := m.renderSessions(leftWidth, m.height)
	right := lipgloss.NewStyle().
		Width(rightWidth).
		Height(m.height).
		Background(lipgloss.Color("#F7F2E8")).
		Render(lipgloss.JoinVertical(
			lipgloss.Left,
			lipgloss.PlaceHorizontal(rightWidth, lipgloss.Center, m.renderHeader(contentWidth)),
			lipgloss.PlaceHorizontal(rightWidth, lipgloss.Center, m.renderConversation(contentWidth)),
			lipgloss.PlaceHorizontal(rightWidth, lipgloss.Center, m.renderInput(contentWidth)),
			lipgloss.PlaceHorizontal(rightWidth, lipgloss.Center, m.renderFooter(contentWidth)),
		))
	base := lipgloss.JoinHorizontal(lipgloss.Top, left, right)
	base = styles.App.Width(m.width).Height(m.height).Render(base)
	if m.settingsOpen {
		return m.renderSettingsModal()
	}
	return base
}

func (m *model) sendPrompt() tea.Cmd {
	if m.running || m.streaming {
		m.status = "上一轮尚未结束"
		return nil
	}

	prompt := strings.TrimSpace(m.input.Value())
	if prompt == "" {
		m.status = "请输入内容"
		return nil
	}

	m.messages = append(m.messages, storage.ChatMessage{
		Role:      "user",
		Text:      prompt,
		Timestamp: time.Now(),
	})
	m.lastAssistant = ""
	m.stopAssistantStream(false)
	m.refreshViewport()
	m.input.Reset()
	m.running = true
	m.status = "请求已发送"

	ctx, cancel := context.WithCancel(context.Background())
	m.cancelRun = cancel
	m.runEvents = m.client.Send(ctx, m.activeSessionID, prompt)
	return m.listenCmd(m.runEvents)
}

func (m *model) loadSessionsCmd() tea.Cmd {
	return func() tea.Msg {
		sessions, err := m.store.LoadSessionIndex(m.config.RecentSessionLimit)
		return sessionsLoadedMsg{sessions: sessions, err: err}
	}
}

func (m *model) loadConversationCmd(sessionID string) tea.Cmd {
	if strings.TrimSpace(sessionID) == "" {
		return func() tea.Msg {
			return conversationLoadedMsg{sessionID: "", messages: nil}
		}
	}
	return func() tea.Msg {
		messages, err := m.store.LoadConversation(sessionID)
		return conversationLoadedMsg{
			sessionID: sessionID,
			messages:  messages,
			err:       err,
		}
	}
}

func (m *model) listenCmd(events <-chan codexui.Event) tea.Cmd {
	if events == nil {
		return nil
	}
	return func() tea.Msg {
		evt, ok := <-events
		return codexEventMsg{event: evt, ok: ok}
	}
}

func (m *model) renderSessions(width, height int) string {
	styles := newStyles(m.width)

	var lines []string
	title := "会话"
	switch m.focus {
	case focusSessions:
		title += " · 列表"
	case focusFilter:
		title += " · 搜索"
	}
	lines = append(lines, styles.SidebarHeader.Width(width-4).Render(lipgloss.JoinVertical(
		lipgloss.Left,
		styles.SidebarTitle.Render("最近会话"),
		styles.SidebarMeta.Render(title),
		styles.SidebarMeta.Render(shortPath(m.config.WorkDir)),
		styles.SidebarMeta.Render("模型 · "+fallbackModel(m.config.Model)),
		"",
		styles.SidebarInput.Width(width-6).Render(m.filter.View()),
	)))
	start, end := m.visibleSessionRange()
	if len(m.filteredSessions) == 0 {
		lines = append(lines, styles.SidebarMeta.Width(width-3).Render(fmt.Sprintf("0 / %d 个会话", len(m.sessions))))
	} else {
		lines = append(lines, styles.SidebarMeta.Width(width-3).Render(fmt.Sprintf("显示 %d-%d / 共 %d", start+1, end, len(m.filteredSessions))))
	}
	lines = append(lines, "")

	if len(m.filteredSessions) == 0 {
		lines = append(lines, styles.SidebarMeta.Render("没有匹配到会话"))
	} else {
		if start > 0 {
			lines = append(lines, styles.SidebarMeta.Width(width-3).Render("↑ 上方还有会话"))
		}
		for i, session := range m.filteredSessions[start:end] {
			absoluteIndex := start + i
			sessionTitle := truncateRunes(session.ThreadName, width-7)
			prefix := "○ "
			metaPrefix := ""
			if absoluteIndex == m.selected {
				prefix = "● "
			}
			timeText := session.UpdatedAt.Local().Format("01-02 15:04")
			metaLine := truncateRunes(strings.TrimSpace(metaPrefix+" "+timeText+" · "+shortID(session.ID)), width-5)
			block := lipgloss.JoinVertical(
				lipgloss.Left,
				styles.SessionName.Width(width-5).Render(prefix + sessionTitle),
				styles.SessionMeta.Width(width-5).Render(metaLine),
			)
			if absoluteIndex == m.selected {
				block = styles.SessionActive.Width(width-3).Render(block)
			} else {
				block = styles.SessionItem.Width(width-3).Render(block)
			}
			lines = append(lines, block)
		}
		if end < len(m.filteredSessions) {
			lines = append(lines, styles.SidebarMeta.Width(width-3).Render("↓ 下方还有会话"))
		}
	}

	return styles.Sidebar.Width(width).Height(height).Render(strings.Join(lines, "\n"))
}

func (m *model) renderHeader(width int) string {
	styles := newStyles(m.width)
	title := "新会话"
	if strings.TrimSpace(m.activeSessionID) != "" {
		title = shortID(m.activeSessionID)
	}
	subtitle := m.activeSessionName
	if subtitle == "" {
		subtitle = "本地终端客户端"
	}
	runStyle := styles.StatusIdle
	runLabel := "idle"
	if m.running {
		runStyle = styles.StatusRunning
		runLabel = "running"
	} else if m.streaming {
		runStyle = styles.StatusRender
		runLabel = "render"
	}

	top := lipgloss.JoinHorizontal(
		lipgloss.Center,
		styles.HeaderBadge.Render("Session"),
		styles.HeaderSpacer.Render(" "),
		styles.HeaderTitle.Render(title),
		styles.HeaderSpacer.Render(" "),
		runStyle.Render(runLabel),
	)
	bottom := lipgloss.JoinHorizontal(
		lipgloss.Center,
		styles.HeaderMeta.Render(truncateRunes(subtitle, max(20, width-20))),
		styles.HeaderSpacer.Render(" "),
		styles.HeaderMeta.Render("· "+shortPath(m.config.WorkDir)),
	)
	return styles.Header.Width(width).Render(lipgloss.JoinVertical(
		lipgloss.Left,
		styles.PanelTitle.Render("会话"),
		top,
		bottom,
	))
}

func (m *model) renderConversation(width int) string {
	styles := newStyles(m.width)
	return styles.ContentPanel.Width(width).Height(m.viewport.Height + 3).Render(lipgloss.JoinVertical(
		lipgloss.Left,
		styles.PanelTitle.Render("对话内容"),
		m.viewport.View(),
	))
}

func (m *model) renderInput(width int) string {
	styles := newStyles(m.width)
	label := "输入"
	if m.focus == focusInput {
		label += " · 当前"
	}
	return styles.InputBox.Width(width).Render(
		lipgloss.JoinVertical(
			lipgloss.Left,
			styles.InputLabel.Render(label),
			styles.InputHint.Render("Enter 换行，Ctrl+S 发送"),
			"",
			m.input.View(),
		),
	)
}

func (m *model) renderFooter(width int) string {
	styles := newStyles(m.width)
	runState := "idle"
	runStyle := styles.StatusIdle
	if m.running {
		runState = "running"
		runStyle = styles.StatusRunning
	} else if m.streaming {
		runState = "rendering"
		runStyle = styles.StatusRender
	}
	left := lipgloss.JoinHorizontal(
		lipgloss.Center,
		runStyle.Render(runState),
		styles.HeaderSpacer.Render(" "),
		styles.FooterMeta.Render(fmt.Sprintf("full-auto %t", m.config.FullAuto)),
		styles.HeaderSpacer.Render(" "),
		styles.FooterMeta.Render(fmt.Sprintf("in %d  cache %d  out %d", m.usage.InputTokens, m.usage.CachedInputTokens, m.usage.OutputTokens)),
	)
	right := styles.FooterHelp.Render("/ 搜索  PgUp/PgDn 翻页  Ctrl+S 发送  Ctrl+N 新会话  Q 退出")
	return styles.Footer.Width(width).Render(
		lipgloss.JoinVertical(
			lipgloss.Left,
			lipgloss.JoinHorizontal(lipgloss.Center, styles.PanelTitle.Render("状态"), styles.HeaderSpacer.Render(" "), left),
			styles.FooterStatus.Render(m.status),
			right,
		),
	)
}

func (m *model) renderSettingsModal() string {
	styles := newStyles(m.width)
	body := lipgloss.JoinVertical(
		lipgloss.Left,
		styles.ModalTitle.Render("设置"),
		"",
		styles.ModalLabel.Render("Model"),
		m.settingsModel.View(),
		"",
		styles.ModalLabel.Render("WorkDir"),
		m.settingsWork.View(),
		"",
		styles.ModalLabel.Render("RecentSessionLimit"),
		m.settingsLimit.View(),
		"",
		styles.ModalLabel.Render(fmt.Sprintf("Full Auto: %t", m.settingsFullAuto)),
		styles.ModalHelp.Render("Tab 切字段  Ctrl+F 切换 Full Auto  Ctrl+S 保存  Esc 关闭"),
	)
	modalWidth := min(max(52, m.width/2), max(52, m.width-6))
	return lipgloss.Place(
		m.width,
		m.height,
		lipgloss.Center,
		lipgloss.Center,
		styles.Modal.Width(modalWidth).Render(body),
	)
}

func (m *model) refreshViewport() {
	styles := newStyles(m.width)
	if len(m.messages) == 0 {
		m.viewport.SetContent(styles.EmptyState.Width(max(26, m.viewport.Width-2)).Render("还没有消息\n\n按 Ctrl+N 新建会话，或直接输入后 Ctrl+S 发送。"))
		m.viewport.GotoBottom()
		return
	}

	var chunks []string
	messageWidth := max(28, min(m.viewport.Width-6, 88))
	for _, message := range m.messages {
		role := "USER"
		style := styles.UserCard
		align := lipgloss.Right
		if message.Role == "assistant" {
			role = "CODEX"
			style = styles.AssistantCard
			align = lipgloss.Left
		}
		header := styles.MessageRole.Render(role) + "  " + styles.MessageTime.Render(message.Timestamp.Format("15:04:05"))
		body := lipgloss.NewStyle().Width(messageWidth).Render(strings.TrimSpace(message.Text))
		card := style.Width(messageWidth + 4).Render(lipgloss.JoinVertical(lipgloss.Left, header, body))
		chunks = append(chunks, lipgloss.PlaceHorizontal(m.viewport.Width, align, card))
	}

	m.viewport.SetContent(strings.Join(chunks, "\n\n"))
	m.viewport.GotoBottom()
}

func (m *model) resize() {
	styles := newStyles(m.width)
	rightWidth := max(40, m.width-styles.LeftPanelWidth-1)
	contentWidth := min(rightWidth-1, 92)
	inputHeight := 8
	headerHeight := 5
	footerHeight := 4

	m.input.SetWidth(max(20, contentWidth-8))
	m.input.SetHeight(4)
	m.filter.Width = max(12, styles.LeftPanelWidth-8)
	m.settingsModel.Width = max(24, min(56, m.width-16))
	m.settingsWork.Width = max(24, min(72, m.width-16))
	m.settingsLimit.Width = 12
	m.viewport.Width = max(24, contentWidth-6)
	m.viewport.Height = max(8, m.height-headerHeight-inputHeight-footerHeight)
}

func (m *model) toggleFocus() {
	if m.focus == focusInput {
		m.focus = focusSessions
		m.input.Blur()
		m.filter.Blur()
		return
	}
	if m.focus == focusFilter {
		m.focus = focusInput
		m.filter.Blur()
		m.input.Focus()
		return
	}
	m.focus = focusInput
	m.input.Focus()
}

func (m *model) cancelCurrentRun() {
	if m.cancelRun != nil {
		m.cancelRun()
		m.cancelRun = nil
	}
	m.runEvents = nil
}

func (m *model) startAssistantStream(message string) tea.Cmd {
	message = strings.TrimSpace(message)
	if message == "" {
		return nil
	}
	if m.streaming && message == m.streamTarget {
		return nil
	}
	if !m.streaming && message == m.lastAssistant {
		return nil
	}

	if m.streamMessageIdx < 0 || m.streamMessageIdx >= len(m.messages) || m.messages[m.streamMessageIdx].Role != "assistant" {
		m.messages = append(m.messages, storage.ChatMessage{
			Role:      "assistant",
			Text:      "",
			Timestamp: time.Now(),
		})
		m.streamMessageIdx = len(m.messages) - 1
	}

	current := ""
	if m.streamMessageIdx >= 0 && m.streamMessageIdx < len(m.messages) {
		current = m.messages[m.streamMessageIdx].Text
	}
	if !strings.HasPrefix(message, current) {
		current = ""
		if m.streamMessageIdx >= 0 && m.streamMessageIdx < len(m.messages) {
			m.messages[m.streamMessageIdx].Text = ""
		}
	}

	m.streaming = true
	m.streamTarget = message
	m.streamVisible = current
	m.lastAssistant = message
	m.refreshViewport()
	return m.streamTickCmd()
}

func (m *model) advanceAssistantStream() tea.Cmd {
	if !m.streaming {
		return nil
	}
	if m.streamMessageIdx < 0 || m.streamMessageIdx >= len(m.messages) {
		m.stopAssistantStream(false)
		return nil
	}

	targetRunes := []rune(m.streamTarget)
	visibleRunes := []rune(m.streamVisible)
	if len(visibleRunes) >= len(targetRunes) {
		m.messages[m.streamMessageIdx].Text = m.streamTarget
		m.refreshViewport()
		return m.finishAssistantStream()
	}

	step := 8
	remaining := len(targetRunes) - len(visibleRunes)
	if remaining > 160 {
		step = 18
	} else if remaining > 80 {
		step = 12
	} else if remaining < 24 {
		step = 4
	}
	next := min(len(targetRunes), len(visibleRunes)+step)
	m.streamVisible = string(targetRunes[:next])
	m.messages[m.streamMessageIdx].Text = m.streamVisible
	m.refreshViewport()

	if next >= len(targetRunes) {
		return m.finishAssistantStream()
	}
	return m.streamTickCmd()
}

func (m *model) finishAssistantStream() tea.Cmd {
	shouldReload := m.pendingReload
	m.stopAssistantStream(true)
	if shouldReload {
		return tea.Batch(m.loadSessionsCmd(), m.loadConversationCmd(m.activeSessionID))
	}
	return nil
}

func (m *model) stopAssistantStream(resetMessageIndex bool) {
	m.streaming = false
	m.streamTarget = ""
	m.streamVisible = ""
	if resetMessageIndex {
		m.streamMessageIdx = -1
	}
}

func (m *model) streamTickCmd() tea.Cmd {
	return tea.Tick(24*time.Millisecond, func(time.Time) tea.Msg {
		return streamTickMsg{}
	})
}

func (m *model) applyFilter() {
	query := strings.TrimSpace(strings.ToLower(m.filter.Value()))
	if query == "" {
		m.filteredSessions = append([]storage.SessionSummary(nil), m.sessions...)
		return
	}

	filtered := make([]storage.SessionSummary, 0, len(m.sessions))
	for _, session := range m.sessions {
		if strings.Contains(strings.ToLower(session.ThreadName), query) || strings.Contains(strings.ToLower(session.ID), query) {
			filtered = append(filtered, session)
		}
	}
	m.filteredSessions = filtered
}

func (m *model) openSettings() {
	m.settingsOpen = true
	m.settingsFocus = 0
	m.settingsModel.SetValue(m.config.Model)
	m.settingsWork.SetValue(m.config.WorkDir)
	m.settingsLimit.SetValue(strconv.Itoa(m.config.RecentSessionLimit))
	m.settingsFullAuto = m.config.FullAuto
	m.syncSettingsFocus()
	m.status = "设置面板已打开"
}

func (m *model) closeSettings(status string) {
	m.settingsOpen = false
	m.settingsModel.Blur()
	m.settingsWork.Blur()
	m.settingsLimit.Blur()
	m.status = status
}

func (m *model) syncSettingsFocus() {
	m.settingsModel.Blur()
	m.settingsWork.Blur()
	m.settingsLimit.Blur()
	switch m.settingsFocus {
	case 0:
		m.settingsModel.Focus()
	case 1:
		m.settingsWork.Focus()
	case 2:
		m.settingsLimit.Focus()
	}
}

func (m *model) updateSettings(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "esc":
		m.closeSettings("已取消设置修改")
		return m, nil
	case "tab":
		m.settingsFocus = (m.settingsFocus + 1) % 3
		m.syncSettingsFocus()
		return m, nil
	case "shift+tab":
		m.settingsFocus = (m.settingsFocus + 2) % 3
		m.syncSettingsFocus()
		return m, nil
	case "ctrl+f":
		m.settingsFullAuto = !m.settingsFullAuto
		m.status = fmt.Sprintf("设置中 Full Auto: %t", m.settingsFullAuto)
		return m, nil
	case "ctrl+s":
		return m, m.saveSettings()
	}

	var cmd tea.Cmd
	switch m.settingsFocus {
	case 0:
		m.settingsModel, cmd = m.settingsModel.Update(msg)
	case 1:
		m.settingsWork, cmd = m.settingsWork.Update(msg)
	case 2:
		m.settingsLimit, cmd = m.settingsLimit.Update(msg)
	}
	return m, cmd
}

func (m *model) saveSettings() tea.Cmd {
	modelValue := strings.TrimSpace(m.settingsModel.Value())
	workDir := strings.TrimSpace(m.settingsWork.Value())
	limitValue := strings.TrimSpace(m.settingsLimit.Value())

	limit, err := strconv.Atoi(limitValue)
	if err != nil || limit <= 0 {
		m.status = "recent_session_limit 必须是正整数"
		return nil
	}
	if workDir == "" {
		m.status = "work_dir 不能为空"
		return nil
	}

	m.config.Model = modelValue
	m.config.WorkDir = workDir
	m.config.RecentSessionLimit = limit
	m.config.FullAuto = m.settingsFullAuto
	m.client = codexui.NewClient(m.config)

	if err := storage.SaveConfig(m.configPath, m.config); err != nil {
		m.status = "保存配置失败: " + err.Error()
		return nil
	}

	m.closeSettings("配置已保存")
	return tea.Batch(m.loadSessionsCmd(), m.loadConversationCmd(m.activeSessionID))
}

func (m *model) visibleSessionCount() int {
	if m.height <= 0 {
		return 6
	}
	available := m.height - 11
	if available < 3 {
		return 1
	}
	count := available / 3
	if count < 1 {
		return 1
	}
	return count
}

func (m *model) clampSessionWindow() {
	total := len(m.filteredSessions)
	if total == 0 {
		m.selected = 0
		m.sessionOffset = 0
		return
	}
	if m.selected < 0 {
		m.selected = 0
	}
	if m.selected >= total {
		m.selected = total - 1
	}

	visible := m.visibleSessionCount()
	maxOffset := max(0, total-visible)
	if m.sessionOffset > maxOffset {
		m.sessionOffset = maxOffset
	}
	if m.sessionOffset < 0 {
		m.sessionOffset = 0
	}
	if m.selected < m.sessionOffset {
		m.sessionOffset = m.selected
	}
	if m.selected >= m.sessionOffset+visible {
		m.sessionOffset = m.selected - visible + 1
	}
	if m.sessionOffset > maxOffset {
		m.sessionOffset = maxOffset
	}
}

func (m *model) visibleSessionRange() (int, int) {
	total := len(m.filteredSessions)
	if total == 0 {
		return 0, 0
	}
	m.clampSessionWindow()
	start := m.sessionOffset
	end := min(total, start+m.visibleSessionCount())
	return start, end
}

func (m *model) selectedSessionID() string {
	if m.selected < 0 || m.selected >= len(m.filteredSessions) {
		return ""
	}
	return m.filteredSessions[m.selected].ID
}

func shortID(id string) string {
	if len(id) >= 8 {
		return id[:8]
	}
	return id
}

func shortPath(path string) string {
	if len(path) <= 28 {
		return path
	}
	return "..." + path[len(path)-25:]
}

func fallbackModel(model string) string {
	if strings.TrimSpace(model) == "" {
		return "default"
	}
	return model
}

func (m *model) sessionNameByID(sessionID string) string {
	for _, session := range m.sessions {
		if session.ID == sessionID {
			return session.ThreadName
		}
	}
	if strings.TrimSpace(sessionID) == "" {
		return "新会话"
	}
	return shortID(sessionID)
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func truncateRunes(s string, limit int) string {
	if limit <= 0 {
		return ""
	}
	r := []rune(s)
	if len(r) <= limit {
		return s
	}
	if limit <= 1 {
		return string(r[:limit])
	}
	return string(r[:limit-1]) + "…"
}
