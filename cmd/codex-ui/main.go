package main

import (
	"flag"
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"

	"codex-ui/internal/app"
	"codex-ui/internal/storage"
)

func main() {
	configPath := flag.String("config", "", "config file path")
	workdir := flag.String("cwd", "", "override working directory")
	model := flag.String("model", "", "override codex model")
	fullAuto := flag.Bool("full-auto", false, "override full-auto mode")
	flag.Parse()

	loaded, err := storage.LoadOrInitConfigWithPath(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "load config failed: %v\n", err)
		os.Exit(1)
	}
	cfg := loaded.Config

	if *workdir != "" {
		cfg.WorkDir = *workdir
	}
	if *model != "" {
		cfg.Model = *model
	}
	if *fullAuto {
		cfg.FullAuto = true
	}

	p := tea.NewProgram(app.NewModel(cfg, loaded.Path), tea.WithAltScreen())
	if _, err := p.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "start ui failed: %v\n", err)
		os.Exit(1)
	}
}
