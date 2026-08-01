-- Usuários sem perfil ficavam com permissão nenhuma: entravam, viam só o chat
-- e a navegação parecia vazia, sem nada explicando o porquê. O estado não é
-- "acesso restrito", é estado quebrado — a criação de usuário passou a sempre
-- atribuir um perfil (ver users.py::_default_profile_id), e aqui os que já
-- existiam são consertados com o mesmo critério:
--   o usuário mais antigo da empresa é o dono dela -> Administrador
--   os demais -> Usuário

WITH primeiro_por_tenant AS (
    SELECT DISTINCT ON (tenant_id) id, tenant_id
      FROM users
     WHERE tenant_id IS NOT NULL
     ORDER BY tenant_id, created_at
)
UPDATE users u
   SET profile_id = p.id
  FROM primeiro_por_tenant f
  JOIN user_profiles p ON p.tenant_id = f.tenant_id AND p.name = 'Administrador'
 WHERE u.id = f.id
   AND u.profile_id IS NULL
   AND NOT u.is_master;

UPDATE users u
   SET profile_id = p.id
  FROM user_profiles p
 WHERE p.tenant_id = u.tenant_id
   AND p.name = 'Usuário'
   AND u.profile_id IS NULL
   AND u.tenant_id IS NOT NULL
   AND NOT u.is_master;
