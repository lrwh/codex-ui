package codexui

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"

	"codex-ui/internal/storage"
)

type EventType string

const (
	EventThreadStarted EventType = "thread_started"
	EventAgentMessage  EventType = "agent_message"
	EventUsage         EventType = "usage"
	EventStatus        EventType = "status"
	EventDone          EventType = "done"
	EventError         EventType = "error"
)

type TokenUsage struct {
	InputTokens       int
	CachedInputTokens int
	OutputTokens      int
}

type Event struct {
	Type     EventType
	ThreadID string
	Message  string
	Usage    TokenUsage
	Err      error
}

type Client struct {
	Binary string
	Config storage.AppConfig
}

func NewClient(cfg storage.AppConfig) *Client {
	return &Client{
		Binary: cfg.CodexPath,
		Config: cfg,
	}
}

func (c *Client) Send(ctx context.Context, sessionID, prompt string) <-chan Event {
	events := make(chan Event, 32)
	go c.run(ctx, sessionID, prompt, events)
	return events
}

func (c *Client) run(ctx context.Context, sessionID, prompt string, events chan<- Event) {
	defer close(events)

	args := c.buildArgs(sessionID, prompt)
	cmd := exec.CommandContext(ctx, c.Binary, args...)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		events <- Event{Type: EventError, Err: err}
		return
	}

	var stderr bytes.Buffer
	cmd.Stderr = &stderr

	if err := cmd.Start(); err != nil {
		events <- Event{Type: EventError, Err: err}
		return
	}

	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)

	for scanner.Scan() {
		if evt := parseLine(scanner.Bytes()); evt != nil {
			events <- *evt
		}
	}

	if err := scanner.Err(); err != nil {
		events <- Event{Type: EventError, Err: err}
		return
	}

	if err := cmd.Wait(); err != nil {
		msg := strings.TrimSpace(stderr.String())
		if msg == "" {
			msg = err.Error()
		}
		events <- Event{Type: EventError, Err: fmt.Errorf("%s", msg)}
		return
	}

	events <- Event{Type: EventDone}
}

func (c *Client) buildArgs(sessionID, prompt string) []string {
	if strings.TrimSpace(sessionID) == "" {
		args := []string{"exec", "--json"}
		if c.Config.SkipGitRepoCheck {
			args = append(args, "--skip-git-repo-check")
		}
		if c.Config.FullAuto {
			args = append(args, "--full-auto")
		}
		if strings.TrimSpace(c.Config.Model) != "" {
			args = append(args, "-m", c.Config.Model)
		}
		if strings.TrimSpace(c.Config.WorkDir) != "" {
			args = append(args, "-C", c.Config.WorkDir)
		}
		return append(args, prompt)
	}

	args := []string{"exec", "resume", "--json"}
	if c.Config.SkipGitRepoCheck {
		args = append(args, "--skip-git-repo-check")
	}
	if c.Config.FullAuto {
		args = append(args, "--full-auto")
	}
	if strings.TrimSpace(c.Config.Model) != "" {
		args = append(args, "-m", c.Config.Model)
	}
	args = append(args, sessionID)
	return append(args, prompt)
}

func parseLine(line []byte) *Event {
	type root struct {
		Type     string          `json:"type"`
		ThreadID string          `json:"thread_id"`
		Item     json.RawMessage `json:"item"`
		Payload  json.RawMessage `json:"payload"`
		Usage    *struct {
			InputTokens       int `json:"input_tokens"`
			CachedInputTokens int `json:"cached_input_tokens"`
			OutputTokens      int `json:"output_tokens"`
		} `json:"usage"`
	}

	var msg root
	if err := json.Unmarshal(line, &msg); err != nil {
		return nil
	}

	switch msg.Type {
	case "thread.started":
		return &Event{Type: EventThreadStarted, ThreadID: msg.ThreadID}
	case "turn.completed":
		if msg.Usage == nil {
			return nil
		}
		return &Event{
			Type: EventUsage,
			Usage: TokenUsage{
				InputTokens:       msg.Usage.InputTokens,
				CachedInputTokens: msg.Usage.CachedInputTokens,
				OutputTokens:      msg.Usage.OutputTokens,
			},
		}
	case "item.completed":
		type item struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}
		var payload item
		if err := json.Unmarshal(msg.Item, &payload); err != nil {
			return nil
		}
		if payload.Type == "agent_message" && strings.TrimSpace(payload.Text) != "" {
			return &Event{Type: EventAgentMessage, Message: payload.Text}
		}
	case "event_msg":
		type payload struct {
			Type    string `json:"type"`
			Message string `json:"message"`
			Info    *struct {
				TotalTokenUsage struct {
					InputTokens       int `json:"input_tokens"`
					CachedInputTokens int `json:"cached_input_tokens"`
					OutputTokens      int `json:"output_tokens"`
				} `json:"total_token_usage"`
			} `json:"info"`
		}
		var body payload
		if err := json.Unmarshal(msg.Payload, &body); err != nil {
			return nil
		}
		switch body.Type {
		case "agent_message":
			if strings.TrimSpace(body.Message) == "" {
				return nil
			}
			return &Event{Type: EventAgentMessage, Message: body.Message}
		case "token_count":
			if body.Info == nil {
				return nil
			}
			return &Event{
				Type: EventUsage,
				Usage: TokenUsage{
					InputTokens:       body.Info.TotalTokenUsage.InputTokens,
					CachedInputTokens: body.Info.TotalTokenUsage.CachedInputTokens,
					OutputTokens:      body.Info.TotalTokenUsage.OutputTokens,
				},
			}
		}
	}

	return nil
}
