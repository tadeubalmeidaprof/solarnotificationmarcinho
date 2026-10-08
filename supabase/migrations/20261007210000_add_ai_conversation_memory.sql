CREATE TABLE IF NOT EXISTS ai_conversation_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_conversation_messages_key_created
    ON ai_conversation_messages (conversation_key, created_at DESC);

ALTER TABLE ai_conversation_messages ENABLE ROW LEVEL SECURITY;
