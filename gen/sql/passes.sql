-- Detective Pass purchases: one row per paid Stripe Checkout Session. Only a
-- sha256 of the lowercased email is kept (for "Restore purchase"). Applied with
-- npx wrangler d1 execute imposter-boards --remote --file gen/sql/passes.sql
CREATE TABLE IF NOT EXISTS passes (
  session_id TEXT PRIMARY KEY,
  email_hash TEXT NOT NULL,
  created INTEGER NOT NULL,
  restores INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS passes_by_email ON passes (email_hash);
