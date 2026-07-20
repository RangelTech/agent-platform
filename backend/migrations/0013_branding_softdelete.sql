-- 0013: tenant branding and resource archiving.

ALTER TABLE tenants ADD COLUMN brand_name TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN brand_logo_url TEXT NOT NULL DEFAULT '';
-- Primary accent color (hex) and preferred theme for the tenant's users.
ALTER TABLE tenants ADD COLUMN brand_color TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN brand_theme TEXT NOT NULL DEFAULT 'dark'
    CHECK (brand_theme IN ('dark', 'light'));

-- Soft-delete for tenant resources (templates already have is_deleted).
ALTER TABLE files ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE datasources ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE secrets ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE ai_services ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
