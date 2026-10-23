-- Restore limit (functions/api/pass/restore.js). Applied once with
-- npx wrangler d1 execute imposter-boards --remote --file gen/sql/passes_restores.sql
ALTER TABLE passes ADD COLUMN restores INTEGER NOT NULL DEFAULT 0;
