-- Rate limiting. A per-user, per-day token meter shared across chat + doc reading.
CREATE TABLE IF NOT EXISTS daily_token_usage (
  user_id TEXT NOT NULL,
  day TEXT NOT NULL,                 -- YYYY-MM-DD (UTC)
  tokens_used INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);
