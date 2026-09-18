-- Teams were global. `teams` had no owner column, and the `/api/v1/teams` routes checked only that
-- a caller was signed in — so any user could list, rename or delete any Instance's teams, and a
-- board's team label was shared state between tenants. This column is the owner.
--
-- NULL is deliberate and means "seeded default, shared by every Instance": the six rows created in
-- 0004 are referenced by demo boards and by anyone who adopted them, so they stay visible. They
-- become read-only once sign-in is configured — a caller may only modify rows carrying their own
-- instance_id (`store.update_team` / `store.delete_team`).
--
-- ALTER TABLE ADD COLUMN only: this must also run on Postgres, where a table rebuild to widen the
-- UNIQUE(name) constraint would need engine-specific SQL. Name uniqueness therefore stays global;
-- the residual is recorded in SECURITY.md.
ALTER TABLE teams ADD COLUMN instance_id TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_teams_instance ON teams(instance_id);
