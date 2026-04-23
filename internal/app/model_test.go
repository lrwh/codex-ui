package app

import (
	"testing"

	"codex-ui/internal/storage"
)

func TestApplyFilter(t *testing.T) {
	m := &model{
		sessions: []storage.SessionSummary{
			{ID: "aaa11111", ThreadName: "codex-ui"},
			{ID: "bbb22222", ThreadName: "java dump"},
			{ID: "ccc33333", ThreadName: "skill test"},
		},
	}

	m.filter.SetValue("java")
	m.applyFilter()

	if len(m.filteredSessions) != 1 {
		t.Fatalf("expected 1 filtered session, got %d", len(m.filteredSessions))
	}
	if m.filteredSessions[0].ThreadName != "java dump" {
		t.Fatalf("unexpected filtered session: %s", m.filteredSessions[0].ThreadName)
	}
}

func TestClampSessionWindow(t *testing.T) {
	m := &model{
		height: 24,
		filteredSessions: []storage.SessionSummary{
			{ID: "1"}, {ID: "2"}, {ID: "3"}, {ID: "4"}, {ID: "5"},
			{ID: "6"}, {ID: "7"}, {ID: "8"}, {ID: "9"}, {ID: "10"},
		},
		selected: 7,
	}

	m.clampSessionWindow()

	if m.visibleSessionCount() != 4 {
		t.Fatalf("expected visible session count 4, got %d", m.visibleSessionCount())
	}
	if m.sessionOffset != 4 {
		t.Fatalf("expected session offset 4, got %d", m.sessionOffset)
	}
	start, end := m.visibleSessionRange()
	if start != 4 || end != 8 {
		t.Fatalf("expected visible range 4..8, got %d..%d", start, end)
	}
}

func TestAssistantStreamLifecycle(t *testing.T) {
	m := &model{
		streamMessageIdx: -1,
	}

	if cmd := m.startAssistantStream("Alpha Beta Gamma"); cmd == nil {
		t.Fatal("expected stream command, got nil")
	}

	for i := 0; i < 32 && m.streaming; i++ {
		m.advanceAssistantStream()
	}

	if m.streaming {
		t.Fatal("expected stream to finish")
	}
	if len(m.messages) != 1 {
		t.Fatalf("expected 1 assistant message, got %d", len(m.messages))
	}
	if m.messages[0].Role != "assistant" {
		t.Fatalf("expected assistant role, got %s", m.messages[0].Role)
	}
	if m.messages[0].Text != "Alpha Beta Gamma" {
		t.Fatalf("unexpected final assistant text: %q", m.messages[0].Text)
	}
	if m.streamMessageIdx != -1 {
		t.Fatalf("expected stream message index reset, got %d", m.streamMessageIdx)
	}
}
