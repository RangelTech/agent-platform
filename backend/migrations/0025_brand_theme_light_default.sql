-- 0025: light theme becomes the platform default for new tenants.
-- ALTER COLUMN SET DEFAULT only changes what future INSERTs get — it does
-- NOT touch brand_theme on tenants that already exist (intentional, see
-- produto-03-chatwoot-branding-locale-tema.md section 4: migrating existing
-- tenants to light is an open product decision, not made here).
ALTER TABLE tenants ALTER COLUMN brand_theme SET DEFAULT 'light';
