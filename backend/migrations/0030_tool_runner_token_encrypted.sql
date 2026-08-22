-- Existing installations may have applied 0029 before token recovery was
-- added. Keep this additive migration safe for both old and fresh databases.
ALTER TABLE tool_runner_tokens
    ADD COLUMN IF NOT EXISTS token_encrypted TEXT;
