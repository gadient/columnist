-- Instance membership & invite allowlist.
-- One row per invited/active user in an Instance. The row is created at INVITE time
-- keyed by email (allowlist), before the Cognito account exists; user_id is bound on
-- first sign-in. Role is 'piu' or 'siu'. The operator is NOT stored here.
--
-- Cap: an outstanding invite reserves a seat, so both 'invited' and 'active'
-- count. (The original cap here, settings.max_users, is superseded by the per-Instance
-- caps max_primary_users / max_secondary_per_primary, enforced in store.py.)

CREATE TABLE IF NOT EXISTS instance_members (
  id TEXT PRIMARY KEY,
  instance_id TEXT NOT NULL,
  email TEXT NOT NULL,                     -- allowlist key, lowercased
  user_id TEXT,                            -- Cognito `sub`; NULL until first login binds it
  role TEXT NOT NULL,                      -- 'piu' | 'siu'
  status TEXT NOT NULL DEFAULT 'invited',  -- 'invited' | 'active'
  invited_by TEXT,                         -- inviter's user_id; NULL for operator-seeded PIUs
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (instance_id, email)
);

CREATE INDEX IF NOT EXISTS idx_instance_members_instance ON instance_members(instance_id);
CREATE INDEX IF NOT EXISTS idx_instance_members_email    ON instance_members(email);
CREATE INDEX IF NOT EXISTS idx_instance_members_user     ON instance_members(user_id);
