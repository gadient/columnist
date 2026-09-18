-- Notes-to-Cards import sessions.
--
-- One import session = one board + one pasted note or uploaded file. The session is
-- permanently bound to the board it was created on; navigating elsewhere does not retarget it.
--
-- Retention: no TTL. Sessions and their excerpts live until
-- their Instance is deleted. No deletion UX for MVP.
--
-- Content-retention boundary: the uploaded file and the full extracted
-- text are NOT retained. Only the structured proposal, minimal supporting excerpts, the input
-- hash, and metadata needed for audit and retry are persisted here.


-- One analysis lifecycle.
CREATE TABLE IF NOT EXISTS note_import_sessions (
  id TEXT PRIMARY KEY,                 -- opaque, server-generated
  instance_id TEXT NOT NULL,           -- tenant boundary; excerpts die with the Instance
  board_id TEXT NOT NULL,              -- the session is permanently bound to this board
  actor_id TEXT,                       -- Cognito `sub`; NULL when Cognito is off (local dev)
  board_version INTEGER NOT NULL,      -- server-authoritative version at analysis time

  -- Input provenance. The bytes themselves are gone; this is what we keep about them.
  input_mode TEXT NOT NULL,            -- 'paste' | 'txt' | 'docx'  (exactly one source)
  original_filename TEXT,              -- metadata only; never affects parsing
  declared_media_type TEXT,            -- what the client claimed
  detected_media_type TEXT,            -- what we detected; mismatch = spoofed
  byte_size INTEGER,
  content_sha256 TEXT,                 -- of the raw input; audit + retry identity

  meeting_date TEXT NOT NULL,          -- anchor for relative dates; NOT file mtime
  user_timezone TEXT,                  -- governs date interpretation

  extracted_char_count INTEGER,
  input_tokens_estimated INTEGER,
  output_tokens_estimated INTEGER,

  -- Reproducibility. Re-running a note is not guaranteed to produce identical language;
  -- applying an approved snapshot IS deterministic. These record what produced this proposal.
  provider TEXT,                       -- 'offline' | 'bedrock' | ...
  model_id TEXT,
  model_version TEXT,
  prompt_version TEXT,
  schema_version TEXT,
  determinism TEXT,

  status TEXT NOT NULL,                -- 'validating'|'analyzing'|'ready'|'failed'
  error_code TEXT,                     -- error code (e.g. DOCX_TRACKED_CHANGES); NULL on success
  partial_failure_count INTEGER NOT NULL DEFAULT 0,
                                       -- recommendations the model returned but we could not read.
                                       -- Surfaced to the user ("1 item couldn't be read") rather than
                                       -- silently dropped.

  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_note_import_sessions_board    ON note_import_sessions(board_id);
CREATE INDEX IF NOT EXISTS idx_note_import_sessions_instance ON note_import_sessions(instance_id);


-- One proposed new card. Never a mutation — `action` is always create_card.
CREATE TABLE IF NOT EXISTS note_import_recommendations (
  id TEXT PRIMARY KEY,                 -- server-generated. The model never supplies an id,
                                       -- so it cannot collide with or forge one.
  session_id TEXT NOT NULL,
  position INTEGER NOT NULL,           -- display order within the session

  -- Immutable. Source excerpts and the original agent output are never changed; a user edit
  -- updates the working copy and never overwrites these.
  model_output_json TEXT NOT NULL,     -- exactly what the model returned, post schema-validation
  evidence_json TEXT NOT NULL,         -- [{excerpt, locator}] — >=1 required

  -- Server-resolved. The model returns only `rawName`; code matches it against the board roster,
  -- which is why owner matching is tested deterministically, not by model evals.
  -- [{rawName, resolution: existing_member|ambiguous|unmatched, memberId, alternativeMemberIds, confidence}]
  assignees_json TEXT NOT NULL DEFAULT '[]',

  working_copy_json TEXT,              -- the user's edits; NULL until first edit
  approved_snapshot_json TEXT,         -- the EXACT values authorized at apply time;
                                       -- NULL until approved. Apply creates this, not a re-reading
                                       -- of the model output.

  state TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'rejected'|'created'|'failed'
  state_actor_id TEXT,
  state_changed_at TEXT,

  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES note_import_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_note_import_recs_session ON note_import_recommendations(session_id);
CREATE INDEX IF NOT EXISTS idx_note_import_recs_state   ON note_import_recommendations(state);


-- Apply audit + idempotency proof.
--
-- Two jobs in one table, separated by `status`:
--   * A 'succeeded' row IS the idempotency record. It is INSERTed inside the same transaction as
--     the card/member creation, so it commits with them or not at all. A retry that finds one
--     returns the original result instead of creating a second card.
--   * 'failed' rows are the audit trail (every attempt is recorded). They are written AFTER the
--     failed transaction rolls back, so they never block a retry: a failed, uncommitted
--     application remains retryable.
--
-- The partial unique index below is what makes duplication impossible BY CONSTRUCTION rather than
-- by careful coding: at most one success per recommendation, unlimited failures.
CREATE TABLE IF NOT EXISTS note_import_applications (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  recommendation_id TEXT NOT NULL,
  instance_id TEXT NOT NULL,
  board_id TEXT NOT NULL,
  actor_id TEXT,

  idempotency_key TEXT NOT NULL,       -- client-supplied; survives a lost response
  approved_snapshot_sha256 TEXT NOT NULL,
                                       -- same rec id replayed with a DIFFERENT hash after success
                                       -- => APPLY_CONFLICT, create nothing
  member_creation_approved INTEGER NOT NULL DEFAULT 0,
                                       -- explicit, separate approval captured in the same UI.
                                       -- Never creates a PIU/SIU — board members only.

  created_card_id TEXT,                -- NULL on failure
  created_member_id TEXT,              -- NULL unless a prerequisite member was approved + created

  attempt_count INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,                -- 'succeeded' | 'failed'
  error_code TEXT,                     -- error code (BOARD_REFERENCE_STALE, APPLY_FAILED, ...)
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_ms INTEGER,

  FOREIGN KEY (session_id) REFERENCES note_import_sessions(id) ON DELETE CASCADE
);

-- At most ONE successful application per (session, recommendation) — enforced by the
-- database, not by application code. Partial (WHERE status = 'succeeded') so failed attempts can
-- accumulate as audit without blocking the retry that eventually succeeds.
CREATE UNIQUE INDEX IF NOT EXISTS idx_note_import_apps_once
  ON note_import_applications(session_id, recommendation_id)
  WHERE status = 'succeeded';

CREATE INDEX IF NOT EXISTS idx_note_import_apps_session ON note_import_applications(session_id);
CREATE INDEX IF NOT EXISTS idx_note_import_apps_rec     ON note_import_applications(recommendation_id);
