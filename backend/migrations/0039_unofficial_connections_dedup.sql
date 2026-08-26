-- Produto-15 -- 26/08/2026, achado ao vivo: checagem de duplicata feita no
-- codigo da extensao (listar -> comparar -> apagar -> criar) nao e atomica.
-- Varios eventos de chrome.cookies.onChanged disparando captura em paralelo
-- (comum quando o provider ja estava logado) faziam N chamadas concorrentes
-- passarem pela checagem AO MESMO TEMPO, antes de qualquer uma terminar de
-- salvar -- resultado real: 9 conexoes duplicadas do Instagram numa unica
-- rodada de teste. Indice unico parcial move a garantia pro banco (atomico
-- de verdade, POST vira upsert via ON CONFLICT em vez de insert cego).

-- Limpa duplicatas existentes antes do indice (senao a criacao falha) --
-- mantem a mais recente de cada grupo (tenant_id, provider, external_label),
-- desativa o resto (soft-delete, mesmo padrao do DELETE da rota).
WITH duplicatas AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY tenant_id, provider, external_label
               ORDER BY updated_at DESC
           ) AS posicao
      FROM tenant_unofficial_connections
     WHERE is_active AND external_label IS NOT NULL
)
UPDATE tenant_unofficial_connections
   SET is_active = false
  FROM duplicatas
 WHERE tenant_unofficial_connections.id = duplicatas.id
   AND duplicatas.posicao > 1;

CREATE UNIQUE INDEX idx_tenant_unofficial_connections_dedup
    ON tenant_unofficial_connections (tenant_id, provider, external_label)
    WHERE is_active AND external_label IS NOT NULL;
