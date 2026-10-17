#!/usr/bin/env bash
# Este repo nao faz deploy. A entrega e pelo workflow Release (tag v*), que
# publica image.tar.gz + release-manifest.json; quem implanta e o vps_rt_infra.
echo "Sem deploy aqui: veja .github/workflows/release.yml e o repo vps_rt_infra." >&2
exit 1
