CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

ALTER TABLE boards ADD COLUMN workspace_id TEXT;

CREATE INDEX IF NOT EXISTS idx_boards_workspace_id ON boards(workspace_id);
