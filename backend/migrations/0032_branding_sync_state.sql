-- RAgentes é a fonte de verdade do branding; a cópia no RAtende é apenas
-- materializada e tem estado explícito para não falhar silenciosamente.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS brand_secondary_color TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS branding_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS branding_sync_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS branding_sync_error TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS branding_synced_at TIMESTAMPTZ;
