-- Audit log of every card column transition — drives cycle-time / flow metrics
CREATE TABLE IF NOT EXISTS card_column_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id TEXT NOT NULL,
  board_id TEXT NOT NULL,
  from_column_id TEXT DEFAULT NULL,
  from_column_title TEXT DEFAULT NULL,
  to_column_id TEXT NOT NULL,
  to_column_title TEXT NOT NULL,
  moved_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cce_card_id  ON card_column_events(card_id);
CREATE INDEX IF NOT EXISTS idx_cce_board_id ON card_column_events(board_id);
CREATE INDEX IF NOT EXISTS idx_cce_moved_at ON card_column_events(moved_at);
