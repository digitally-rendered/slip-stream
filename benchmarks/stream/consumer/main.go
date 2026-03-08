// Event consumer service for stream performance testing.
//
// Subscribes to Kafka/NATS/Redis topics and exposes a simple HTTP API
// for k6 to poll event delivery. Supports multiple broker backends
// selected via the CONSUMER_BACKEND environment variable.
//
// Endpoints:
//
//	GET /events?topic=X&limit=N  — return buffered events for a topic
//	GET /stats                   — return message counts per topic
//	GET /health                  — liveness probe
//	DELETE /reset                — clear all buffered events
//
// Usage:
//
//	CONSUMER_BACKEND=nats NATS_URL=nats://localhost:4222 go run ./benchmarks/stream/consumer
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"
)

type event struct {
	Topic     string    `json:"topic"`
	Key       string    `json:"key"`
	Payload   string    `json:"payload"`
	Received  time.Time `json:"received_at"`
	Timestamp float64   `json:"event_timestamp,omitempty"`
}

type store struct {
	mu     sync.RWMutex
	events map[string][]event // topic -> events
}

func newStore() *store {
	return &store{events: make(map[string][]event)}
}

func (s *store) add(topic, key, payload string, eventTimestamp float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events[topic] = append(s.events[topic], event{
		Topic:     topic,
		Key:       key,
		Payload:   payload,
		Received:  time.Now(),
		Timestamp: eventTimestamp,
	})
}

func (s *store) get(topic string, limit int) []event {
	s.mu.RLock()
	defer s.mu.RUnlock()
	evts := s.events[topic]
	if limit > 0 && limit < len(evts) {
		evts = evts[len(evts)-limit:]
	}
	out := make([]event, len(evts))
	copy(out, evts)
	return out
}

func (s *store) stats() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	m := make(map[string]int, len(s.events))
	for k, v := range s.events {
		m[k] = len(v)
	}
	return m
}

func (s *store) reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = make(map[string][]event)
}

func main() {
	port := envOr("CONSUMER_PORT", "9090")
	backend := envOr("CONSUMER_BACKEND", "stub")

	st := newStore()

	// TODO: Wire real consumers based on backend
	// For now, the consumer receives events via POST /ingest (push model)
	// which both slip-stream and stellar-drive can use via HTTP webhook adapter.
	log.Printf("event-consumer starting on :%s backend=%s", port, backend)

	mux := http.NewServeMux()

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})

	mux.HandleFunc("GET /events", func(w http.ResponseWriter, r *http.Request) {
		topic := r.URL.Query().Get("topic")
		if topic == "" {
			http.Error(w, "topic required", http.StatusBadRequest)
			return
		}
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		evts := st.get(topic, limit)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(evts)
	})

	mux.HandleFunc("GET /stats", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(st.stats())
	})

	mux.HandleFunc("DELETE /reset", func(w http.ResponseWriter, _ *http.Request) {
		st.reset()
		w.WriteHeader(http.StatusNoContent)
	})

	// Push-based ingestion endpoint for webhook/HTTP adapters
	mux.HandleFunc("POST /ingest", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Topic     string  `json:"topic"`
			Key       string  `json:"key"`
			Payload   string  `json:"payload"`
			Timestamp float64 `json:"timestamp"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if body.Topic == "" {
			http.Error(w, "topic required", http.StatusBadRequest)
			return
		}
		st.add(body.Topic, body.Key, body.Payload, body.Timestamp)
		w.WriteHeader(http.StatusAccepted)
	})

	srv := &http.Server{
		Addr:         fmt.Sprintf(":%s", port),
		Handler:      mux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	log.Fatal(srv.ListenAndServe())
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
