-- 0051 (spec 10, H-2): o pacote do template aceita arquivos de qualquer tipo
-- (PDF de cardapio, planilha, imagem...) na pasta reservada arquivos/.

-- Versao (imutavel): o texto continua em `content`; arquivo binario vai em
-- `content_bin` com `content` = '' (leitores antigos nao quebram). O sha256 e
-- sempre dos BYTES do arquivo.
ALTER TABLE template_skill_files DROP CONSTRAINT IF EXISTS template_skill_files_kind_check;
ALTER TABLE template_skill_files ADD CONSTRAINT template_skill_files_kind_check
    CHECK (kind IN ('agent', 'skill', 'reference', 'script', 'template', 'asset'));
ALTER TABLE template_skill_files ADD COLUMN IF NOT EXISTS is_binary BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE template_skill_files ADD COLUMN IF NOT EXISTS content_bin BYTEA;
ALTER TABLE template_skill_files ADD COLUMN IF NOT EXISTS mime TEXT;

-- Rascunho: os arquivos de arquivos/ ficam numa tabela propria para que salvar o
-- texto do rascunho nao reescreva (nem apague) os binarios, e vice-versa.
CREATE TABLE IF NOT EXISTS template_draft_assets (
    template_id UUID NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    content BYTEA NOT NULL,
    mime TEXT NOT NULL DEFAULT 'application/octet-stream',
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    updated_by UUID REFERENCES users (id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (template_id, path)
);
