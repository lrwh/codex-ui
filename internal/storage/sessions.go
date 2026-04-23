package storage

import (
	"bufio"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

var sessionIDPattern = regexp.MustCompile(`[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`)

type SessionSummary struct {
	ID         string
	ThreadName string
	UpdatedAt  time.Time
}

type ChatMessage struct {
	Role      string
	Text      string
	Timestamp time.Time
}

type SessionStore struct {
	codexHome string
	mu        sync.Mutex
	fileIndex map[string]string
}

func NewSessionStore(codexHome string) *SessionStore {
	return &SessionStore{
		codexHome: codexHome,
		fileIndex: map[string]string{},
	}
}

func (s *SessionStore) LoadSessionIndex(limit int) ([]SessionSummary, error) {
	indexPath := filepath.Join(s.codexHome, "session_index.jsonl")
	file, err := os.Open(indexPath)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)

	type indexItem struct {
		ID         string `json:"id"`
		ThreadName string `json:"thread_name"`
		UpdatedAt  string `json:"updated_at"`
	}

	var sessions []SessionSummary
	for scanner.Scan() {
		var item indexItem
		if err := json.Unmarshal(scanner.Bytes(), &item); err != nil {
			continue
		}

		updatedAt, _ := time.Parse(time.RFC3339Nano, item.UpdatedAt)
		sessions = append(sessions, SessionSummary{
			ID:         item.ID,
			ThreadName: fallbackThreadName(item.ThreadName, item.ID),
			UpdatedAt:  updatedAt,
		})
	}

	if err := scanner.Err(); err != nil {
		return nil, err
	}

	sort.Slice(sessions, func(i, j int) bool {
		return sessions[i].UpdatedAt.After(sessions[j].UpdatedAt)
	})

	if limit > 0 && len(sessions) > limit {
		sessions = sessions[:limit]
	}
	return sessions, nil
}

func (s *SessionStore) LoadConversation(sessionID string) ([]ChatMessage, error) {
	path, err := s.findSessionFile(sessionID)
	if err != nil {
		return nil, err
	}

	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)

	type envelope struct {
		Timestamp string          `json:"timestamp"`
		Type      string          `json:"type"`
		Payload   json.RawMessage `json:"payload"`
	}
	type contentPart struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	type responseItem struct {
		Type    string        `json:"type"`
		Role    string        `json:"role"`
		Content []contentPart `json:"content"`
	}

	var messages []ChatMessage
	for scanner.Scan() {
		var env envelope
		if err := json.Unmarshal(scanner.Bytes(), &env); err != nil {
			continue
		}
		if env.Type != "response_item" {
			continue
		}

		var item responseItem
		if err := json.Unmarshal(env.Payload, &item); err != nil {
			continue
		}
		if item.Type != "message" || (item.Role != "user" && item.Role != "assistant") {
			continue
		}

		var parts []string
		for _, part := range item.Content {
			if strings.TrimSpace(part.Text) == "" {
				continue
			}
			parts = append(parts, part.Text)
		}
		if len(parts) == 0 {
			continue
		}

		timestamp, _ := time.Parse(time.RFC3339Nano, env.Timestamp)
		messages = append(messages, ChatMessage{
			Role:      item.Role,
			Text:      strings.Join(parts, "\n"),
			Timestamp: timestamp,
		})
	}

	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return messages, nil
}

func (s *SessionStore) findSessionFile(sessionID string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if path, ok := s.fileIndex[sessionID]; ok {
		return path, nil
	}

	root := filepath.Join(s.codexHome, "sessions")
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d == nil || d.IsDir() {
			return err
		}
		match := sessionIDPattern.FindString(filepath.Base(path))
		if match == "" {
			return nil
		}
		s.fileIndex[match] = path
		return nil
	})
	if err != nil {
		return "", err
	}

	if path, ok := s.fileIndex[sessionID]; ok {
		return path, nil
	}

	return "", errors.New("session file not found")
}

func fallbackThreadName(threadName, sessionID string) string {
	if strings.TrimSpace(threadName) != "" {
		return threadName
	}
	if len(sessionID) >= 8 {
		return "Session " + sessionID[:8]
	}
	return "Unnamed Session"
}
