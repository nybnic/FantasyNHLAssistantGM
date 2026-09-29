-- Telegram updates waiting for a workflow run to read them.
CREATE TABLE IF NOT EXISTS updates (update_id INTEGER PRIMARY KEY, body TEXT NOT NULL);
-- dispatch_error: the last failure starting a run (NULL once one succeeds).
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
