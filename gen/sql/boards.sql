-- Friends boards: one row per player per board per day. Applied with
-- npx wrangler d1 execute imposter-boards --remote --file gen/sql/boards.sql
CREATE TABLE IF NOT EXISTS scores (
  board TEXT NOT NULL,
  date TEXT NOT NULL,
  pid TEXT NOT NULL,
  name TEXT NOT NULL,
  score INTEGER NOT NULL,
  time REAL NOT NULL,
  won INTEGER NOT NULL,
  grid TEXT NOT NULL,
  created INTEGER NOT NULL,
  PRIMARY KEY (board, date, pid)
);
CREATE INDEX IF NOT EXISTS scores_by_date ON scores (date);
