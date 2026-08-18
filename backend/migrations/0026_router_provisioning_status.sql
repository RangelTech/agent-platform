-- 0026: visible status for the automatic 9Router provisioning triggered by
-- `create_tenant` (infra-06). Provisioning runs in the background (SSH +
-- DNS + TLS on the VPS takes minutes) — this column is how the "Serviços de
-- IA" screen tells the admin apart pending / running / done / broken instead
-- of just "not provisioned yet" forever.

ALTER TABLE tenants ADD COLUMN router_provisioning_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (router_provisioning_status IN ('pending', 'provisioning', 'ready', 'failed'));
-- Only set when status = 'failed'; kept short, it is shown to the admin.
ALTER TABLE tenants ADD COLUMN router_provisioning_error TEXT;

-- Backfill: tenants that already have a registered instance (provisioned by
-- hand before this column existed, e.g. loja-demo) are 'ready', not 'pending'.
UPDATE tenants
   SET router_provisioning_status = 'ready'
 WHERE id IN (SELECT tenant_id FROM tenant_routers WHERE is_active);
