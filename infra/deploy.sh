#!/usr/bin/env bash
# O kernel saiu deste repo (agent-llm mega spec, infra-05) — virou o repo
# `kernel-llm`, com deploy próprio (Cloud Run, Workload Identity Federation).
# Ver https://github.com/LucasRangelSSouza/kernel-llm/blob/main/infra/deploy.sh
#
# Backend+frontend deployam na VPS (rangeltech.net), via
# infra/docker-compose.prod.yml + .github/workflows/deploy-vps.yml deste
# repo — não é um script manual, é a pipeline de CI.
echo "Nada pra deployar por aqui: kernel foi pro repo kernel-llm," >&2
echo "backend+frontend deployam via .github/workflows/deploy-vps.yml (push em main)." >&2
exit 1
