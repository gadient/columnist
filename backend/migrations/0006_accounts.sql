-- Accounts & collaboration. Tenant isolation builds on these.
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,            -- Cognito `sub`
  email TEXT,
  display_name TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_members (
  workspace_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',   -- 'owner' | 'member'
  invited_by TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (workspace_id, user_id)
);

ALTER TABLE workspaces ADD COLUMN owner_id TEXT;

CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id);
CREATE INDEX IF NOT EXISTS idx_workspace_members_ws ON workspace_members(workspace_id);
