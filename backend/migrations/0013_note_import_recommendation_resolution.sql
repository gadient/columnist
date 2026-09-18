-- Notes-to-Cards: persist the server-derived fields that a note-free GET cannot recompute
-- (a proposal must survive a refresh).
--
-- The import deliberately does NOT retain the extracted note. That makes two resolved fields
-- unrecomputable on read, so they are stored at analysis time instead of derived later:
--
--   * blocked_reasons_json — computed by resolution against the note (an evidence excerpt must be
--     locatable in it). The note is gone by read time, so the verdict must be persisted. It is also
--     the definition of a clean card (empty ⇒ bulk-addable), so storing it makes "clean vs
--     flagged" queryable rather than recomputed.
--   * target_column_id — the user's stamped destination. Neither the model's nor derivable
--     from the note; the user may later change it per card during review.
--   * requires_new_member_named — surfaces the Create-member prerequisite without re-running
--     resolution.
--
-- model_output_json (immutable model output) and assignees_json (resolved owners) already exist on
-- this table from 0012; these three complete a faithful, note-free reconstruction of a
-- RecommendationView.

ALTER TABLE note_import_recommendations ADD COLUMN target_column_id TEXT;
ALTER TABLE note_import_recommendations ADD COLUMN blocked_reasons_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE note_import_recommendations ADD COLUMN requires_new_member_named TEXT;
