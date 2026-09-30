PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS network_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ping REAL NOT NULL,
  jitter REAL NOT NULL,
  download_speed REAL NOT NULL,
  upload_speed REAL NOT NULL,
  timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_queue (
  action_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  title TEXT NOT NULL,
  priority TEXT NOT NULL,
  risk TEXT NOT NULL,
  description TEXT NOT NULL,
  approval_state TEXT NOT NULL DEFAULT 'pending',
  outcome TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL UNIQUE
);
