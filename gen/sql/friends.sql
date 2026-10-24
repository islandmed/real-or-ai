-- Friends leaderboard (functions/api/friends.js): opening someone's share link
-- makes the two players friends for good, stored both ways. Applied once with
-- npx wrangler d1 execute imposter-boards --remote --file gen/sql/friends.sql
CREATE TABLE IF NOT EXISTS friends (
  a TEXT NOT NULL,
  b TEXT NOT NULL,
  created INTEGER NOT NULL,
  PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS scores_by_pid ON scores (pid, date);
