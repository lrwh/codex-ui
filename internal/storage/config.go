package storage

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type AppConfig struct {
	CodexPath          string `json:"codex_path"`
	CodexHome          string `json:"codex_home"`
	WorkDir            string `json:"work_dir"`
	Model              string `json:"model"`
	FullAuto           bool   `json:"full_auto"`
	SkipGitRepoCheck   bool   `json:"skip_git_repo_check"`
	RecentSessionLimit int    `json:"recent_session_limit"`
}

type LoadedConfig struct {
	Path   string
	Config AppConfig
}

func DefaultConfig() AppConfig {
	home, _ := os.UserHomeDir()
	cwd, _ := os.Getwd()

	return AppConfig{
		CodexPath:          resolveCodexPath(),
		CodexHome:          filepath.Join(home, ".codex"),
		WorkDir:            cwd,
		Model:              "",
		FullAuto:           true,
		SkipGitRepoCheck:   true,
		RecentSessionLimit: 30,
	}
}

func DefaultConfigPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".config", "codex-ui", "config.json")
}

func LoadOrInitConfig(path string) (AppConfig, error) {
	loaded, err := LoadOrInitConfigWithPath(path)
	if err != nil {
		return AppConfig{}, err
	}
	return loaded.Config, nil
}

func LoadOrInitConfigWithPath(path string) (LoadedConfig, error) {
	if path == "" {
		path = DefaultConfigPath()
	}
	path = expandHome(path)

	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return LoadedConfig{}, err
	}

	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		cfg := DefaultConfig()
		if err := SaveConfig(path, cfg); err != nil {
			return LoadedConfig{}, err
		}
		return LoadedConfig{Path: path, Config: cfg}, nil
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return LoadedConfig{}, err
	}

	cfg := DefaultConfig()
	if len(data) == 0 {
		return LoadedConfig{Path: path, Config: cfg}, nil
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return LoadedConfig{}, err
	}

	cfg.CodexPath = coalesce(cfg.CodexPath, resolveCodexPath())
	cfg.CodexHome = expandHome(coalesce(cfg.CodexHome, DefaultConfig().CodexHome))
	cfg.WorkDir = expandHome(coalesce(cfg.WorkDir, DefaultConfig().WorkDir))
	if cfg.RecentSessionLimit <= 0 {
		cfg.RecentSessionLimit = 30
	}

	return LoadedConfig{Path: path, Config: cfg}, nil
}

func SaveConfig(path string, cfg AppConfig) error {
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(path, data, 0o644)
}

func resolveCodexPath() string {
	if path, err := exec.LookPath("codex"); err == nil {
		return path
	}
	return "codex"
}

func expandHome(path string) string {
	if path == "" || !strings.HasPrefix(path, "~/") {
		return path
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return path
	}
	return filepath.Join(home, strings.TrimPrefix(path, "~/"))
}

func coalesce(current, fallback string) string {
	if strings.TrimSpace(current) != "" {
		return current
	}
	return fallback
}
