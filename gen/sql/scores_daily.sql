-- Daily score spread: each player's first result per day, for the results
-- screen's "you beat X% of today's detectives" line and curve. Applied with
-- npx wrangler d1 execute imposter-boards --remote --file gen/sql/scores_daily.sql
CREATE TABLE IF NOT EXISTS scores_daily (
  date TEXT NOT NULL,
  pid TEXT NOT NULL,
  score INTEGER NOT NULL,
  time REAL NOT NULL,
  won INTEGER NOT NULL,
  created INTEGER NOT NULL,
  PRIMARY KEY (date, pid)
) WITHOUT ROWID;
