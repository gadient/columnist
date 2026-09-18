-- Teams: named functional groups that own boards
CREATE TABLE IF NOT EXISTS teams (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  color TEXT NOT NULL DEFAULT 'bg-indigo-500',
  jira_prefix TEXT DEFAULT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO teams (id, name, color, jira_prefix, description, created_at, updated_at) VALUES
  ('team_eng',     'Engineering', 'bg-blue-500',   'ENG',   'Core platform and infrastructure',     datetime('now'), datetime('now')),
  ('team_product', 'Product',     'bg-purple-500', 'PM',    'Product management and roadmap',       datetime('now'), datetime('now')),
  ('team_design',  'Design',      'bg-pink-500',   'DES',   'UX and visual design',                 datetime('now'), datetime('now')),
  ('team_legal',   'Legal',       'bg-yellow-600', 'LEGAL', 'Legal and compliance',                 datetime('now'), datetime('now')),
  ('team_mkt',     'Marketing',   'bg-green-500',  'MKT',   'Growth and marketing',                 datetime('now'), datetime('now')),
  ('team_sales',   'Sales',       'bg-orange-500', 'SALES', 'Revenue and sales',                    datetime('now'), datetime('now'));

ALTER TABLE boards ADD COLUMN team_id TEXT DEFAULT NULL REFERENCES teams(id);
