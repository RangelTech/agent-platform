#!/usr/bin/env bash
# O kernel saiu deste repo (agent-llm mega spec, infra-05) — virou o repo
# `kernel-llm`, com deploy próprio (Cloud Run).
# Ver https://github.com/LucasRangelSSouza/kernel-llm/blob/main/infra/deploy.sh
#
# Backend (SPA embutida, _mount_spa) saiu da VPS em 21/08/2026 (infra-01) —
# cutover completo pro Cloud Run, serviço agent-llm-backend. Deploy é
# `./infra/deploy-cloudrun.sh` (gcloud manual, não gated por CI ainda —
# achado real, ver infra-01/memoria.md). VPS não roda mais nada deste repo.
echo "Nada pra deployar por aqui: kernel foi pro repo kernel-llm," >&2
echo "backend (com frontend embutido) deploya via ./infra/deploy-cloudrun.sh (gcloud, manual)." >&2
exit 1
