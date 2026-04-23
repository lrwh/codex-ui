package app

import "github.com/charmbracelet/lipgloss"

type styles struct {
	LeftPanelWidth int
	App            lipgloss.Style
	Sidebar        lipgloss.Style
	SidebarSection lipgloss.Style
	SidebarHeader  lipgloss.Style
	SidebarTitle   lipgloss.Style
	SidebarMeta    lipgloss.Style
	SidebarInput   lipgloss.Style
	SessionItem    lipgloss.Style
	SessionActive  lipgloss.Style
	SessionName    lipgloss.Style
	SessionMeta    lipgloss.Style
	Header         lipgloss.Style
	HeaderTitle    lipgloss.Style
	HeaderMeta     lipgloss.Style
	HeaderSpacer   lipgloss.Style
	HeaderBadge    lipgloss.Style
	PanelTitle     lipgloss.Style
	ContentPanel   lipgloss.Style
	InputBox       lipgloss.Style
	InputLabel     lipgloss.Style
	InputHint      lipgloss.Style
	Footer         lipgloss.Style
	FooterMeta     lipgloss.Style
	FooterStatus   lipgloss.Style
	FooterHelp     lipgloss.Style
	StatusIdle     lipgloss.Style
	StatusRunning  lipgloss.Style
	StatusRender   lipgloss.Style
	UserCard       lipgloss.Style
	AssistantCard  lipgloss.Style
	MessageRole    lipgloss.Style
	MessageTime    lipgloss.Style
	EmptyState     lipgloss.Style
	Modal          lipgloss.Style
	ModalTitle     lipgloss.Style
	ModalLabel     lipgloss.Style
	ModalHelp      lipgloss.Style
}

func newStyles(width int) styles {
	leftWidth := 29
	if width > 140 {
		leftWidth = 31
	}

	return styles{
		LeftPanelWidth: leftWidth,
		App: lipgloss.NewStyle().
			Background(lipgloss.Color("#F7F2E8")).
			Foreground(lipgloss.Color("#2E241B")),
		Sidebar: lipgloss.NewStyle().
			Padding(1, 1).
			BorderRight(true).
			BorderForeground(lipgloss.Color("#E2D3C1")).
			Background(lipgloss.Color("#F5EFE3")).
			Foreground(lipgloss.Color("#2E241B")),
		SidebarSection: lipgloss.NewStyle().
			PaddingBottom(1).
			MarginBottom(1).
			BorderBottom(true).
			BorderForeground(lipgloss.Color("#E8DCCD")),
		SidebarHeader: lipgloss.NewStyle().
			PaddingBottom(1).
			MarginBottom(1),
		SidebarTitle: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#3D3024")),
		SidebarMeta: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#7E6B59")),
		SidebarInput: lipgloss.NewStyle().
			Padding(0, 1).
			BorderStyle(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#DCC8B2")).
			Background(lipgloss.Color("#FFFCF8")).
			Foreground(lipgloss.Color("#3D3024")),
		SessionItem: lipgloss.NewStyle().
			Padding(0, 1).
			MarginBottom(1).
			Foreground(lipgloss.Color("#2E241B")),
		SessionActive: lipgloss.NewStyle().
			Padding(0, 1).
			MarginBottom(1).
			Background(lipgloss.Color("#FFF8EF")).
			Foreground(lipgloss.Color("#2E241B")),
		SessionName: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#23180F")),
		SessionMeta: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#8C735F")),
		Header: lipgloss.NewStyle().
			Padding(1, 2).
			MarginBottom(1).
			BorderStyle(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#E2D3C1")).
			Background(lipgloss.Color("#FFFDF9")).
			Foreground(lipgloss.Color("#2E241B")),
		HeaderTitle: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#23180F")),
		HeaderMeta: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#7E6B59")),
		HeaderSpacer: lipgloss.NewStyle().
			Width(2),
		HeaderBadge: lipgloss.NewStyle().
			Padding(0, 1).
			Bold(true).
			Foreground(lipgloss.Color("#FFFFFF")).
			Background(lipgloss.Color("#D97732")),
		PanelTitle: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#3D3024")),
		ContentPanel: lipgloss.NewStyle().
			Padding(1, 2).
			MarginBottom(1).
			BorderStyle(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#E2D3C1")).
			Background(lipgloss.Color("#FFFDF9")),
		InputBox: lipgloss.NewStyle().
			Padding(1, 2).
			MarginBottom(1).
			BorderStyle(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#E2D3C1")).
			Background(lipgloss.Color("#FFFDF9")),
		InputLabel: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#285E52")),
		InputHint: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#8C735F")),
		Footer: lipgloss.NewStyle().
			Padding(1, 2, 0, 2).
			Foreground(lipgloss.Color("#2E241B")),
		FooterMeta: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#6F5B4A")),
		FooterStatus: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#3D3024")),
		FooterHelp: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#8C735F")),
		StatusIdle: lipgloss.NewStyle().
			Padding(0, 1).
			Foreground(lipgloss.Color("#185B4F")).
			Background(lipgloss.Color("#DDEFE7")),
		StatusRunning: lipgloss.NewStyle().
			Padding(0, 1).
			Foreground(lipgloss.Color("#9A4B1A")).
			Background(lipgloss.Color("#FBE7D3")),
		StatusRender: lipgloss.NewStyle().
			Padding(0, 1).
			Foreground(lipgloss.Color("#7B5D1F")).
			Background(lipgloss.Color("#F4E7B7")),
		UserCard: lipgloss.NewStyle().
			Padding(1, 2).
			BorderStyle(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#E7D7C2")).
			Background(lipgloss.Color("#FFF4E1")).
			Foreground(lipgloss.Color("#6D471E")),
		AssistantCard: lipgloss.NewStyle().
			Padding(1, 2).
			BorderStyle(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#E7D7C2")).
			Background(lipgloss.Color("#FFFFFF")).
			Foreground(lipgloss.Color("#2E241B")),
		MessageRole: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#285E52")),
		MessageTime: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#9A866F")),
		EmptyState: lipgloss.NewStyle().
			Padding(3, 3).
			BorderStyle(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#E2D3C1")).
			Background(lipgloss.Color("#FFFDF9")).
			Foreground(lipgloss.Color("#7E6B59")),
		Modal: lipgloss.NewStyle().
			Padding(1, 2).
			BorderStyle(lipgloss.DoubleBorder()).
			BorderForeground(lipgloss.Color("#2563EB")).
			Background(lipgloss.Color("#FFFFFF")).
			Foreground(lipgloss.Color("#0F172A")),
		ModalTitle: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#1D4ED8")),
		ModalLabel: lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.Color("#0F766E")),
		ModalHelp: lipgloss.NewStyle().
			Foreground(lipgloss.Color("#475569")),
	}
}
