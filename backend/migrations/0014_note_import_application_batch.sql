-- Notes-to-Cards: group the cards a single bulk create produced, so undo can target exactly them.
--
-- A per-card `Approve & create` has no batch — the column is NULL for those. `Add the clean ones`
-- stamps one batch_id on every application it commits, and undo removes exactly the cards of one
-- batch (leaving any the user has since edited or moved). Nullable because the per-card path, which
-- predates this, has no batch and must keep working unchanged.

ALTER TABLE note_import_applications ADD COLUMN batch_id TEXT;

CREATE INDEX IF NOT EXISTS idx_note_import_apps_batch ON note_import_applications(batch_id);
