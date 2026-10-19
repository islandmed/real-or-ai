-- Pick counts: how many one-guess players tapped each card, per round per day.
-- Feeds the "% of detectives caught this AI fake" line once a round has enough
-- plays. Applied with
-- npx wrangler d1 execute imposter-boards --remote --file gen/sql/picks.sql
CREATE TABLE IF NOT EXISTS picks (
  date TEXT NOT NULL,
  round INTEGER NOT NULL,
  card TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (date, round, card)
) WITHOUT ROWID;
