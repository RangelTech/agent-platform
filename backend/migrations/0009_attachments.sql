-- 0009: chat attachments (image/audio/file) recorded on the user message.
ALTER TABLE chat_messages ADD COLUMN attachments JSONB NOT NULL DEFAULT '[]'::jsonb;
