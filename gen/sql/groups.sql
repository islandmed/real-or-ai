-- Group boards (functions/api/groups.js): a named board with one link for a
-- whole group chat. Applied once with
-- npx wrangler d1 execute imposter-boards --remote --file gen/sql/groups.sql
CREATE TABLE IF NOT EXISTS groups (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  owner TEXT NOT NULL,
  created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS groups_by_owner ON groups (owner);
CREATE TABLE IF NOT EXISTS group_members (
  g TEXT NOT NULL,
  pid TEXT NOT NULL,
  joined INTEGER NOT NULL,
  PRIMARY KEY (g, pid)
);
CREATE INDEX IF NOT EXISTS group_members_by_pid ON group_members (pid, joined);
