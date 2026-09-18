-- Backfill team ownership for databases created before 0016.
--
-- 0016 added `teams.instance_id` and left every existing row NULL. On a fresh database those are
-- only the six defaults seeded by 0004, but on an upgraded one a NULL is just as likely to be a
-- team somebody created — and the first version of the scoping rule read NULL as "shared with
-- every Instance", which would have published one tenant's teams to all the others.
--
-- The owner can usually be recovered: a team is referenced by `boards.team_id`, and every board
-- carries an instance_id (0008). Where every board pointing at a team belongs to one Instance,
-- that Instance is the owner. Where none do, or more than one does, the row stays NULL and the
-- application treats it as invisible rather than guessing (`store.list_teams`).
--
-- The six seeded ids are excluded: they are shared on purpose.
--
-- Plain correlated subqueries only — this must run on SQLite and PostgreSQL alike.
UPDATE teams
   SET instance_id = (
         SELECT MIN(b.instance_id)
           FROM boards b
          WHERE b.team_id = teams.id
            AND b.instance_id IS NOT NULL
       )
 WHERE instance_id IS NULL
   AND id NOT IN ('team_eng', 'team_product', 'team_design', 'team_legal', 'team_mkt', 'team_sales')
   AND (
         SELECT COUNT(DISTINCT b.instance_id)
           FROM boards b
          WHERE b.team_id = teams.id
            AND b.instance_id IS NOT NULL
       ) = 1;
