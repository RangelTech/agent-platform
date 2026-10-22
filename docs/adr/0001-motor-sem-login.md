# ADR 0001: o motor não autentica pessoas

Status: aceita (20/10/2026, spec 10).

**Contexto.** O produto tinha dois logins: o do motor e o do Chatwoot. Isso duplicava usuários, senhas e permissões.

**Decisão.** O Chatwoot é dono de identidade e permissões. O motor vira um serviço interno, sem login. Só aceita chamadas com contexto assinado pelo Rails (`UI_CONTEXT_SECRET`) e as chamadas de máquina já existentes (`/v1/messages`, `/internal/*`).

**Consequências.** O cadastro e as sessões do motor saem no corte U-6. A pasta `frontend/` é removida. Toda rota nova do motor precisa validar o contexto e filtrar por `tenant_id`.
