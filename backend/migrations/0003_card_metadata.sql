-- Card metadata: effort estimation, JIRA integration, and dependency tracking
ALTER TABLE cards ADD COLUMN story_points INTEGER DEFAULT NULL;
ALTER TABLE cards ADD COLUMN jira_key TEXT DEFAULT NULL;
ALTER TABLE cards ADD COLUMN labels_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE cards ADD COLUMN blocked_by_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE cards ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_cards_jira_key ON cards(jira_key);
