# Imagem oficial já traz Chromium + todas as deps do sistema pra rodar --
# montar isso na mão com apt-get é frágil e muda a cada versão do Chromium.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# Achado real 25/08/2026: Chromium headless trava na verificação da
# Cloudflare ("Verify you are human"). Rodar headful precisa de um
# display de verdade mesmo sem monitor -- xvfb-run cria um virtual.
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY oauth-browser/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Achado real 25/08/2026: mesmo headful, o Cloudflare Turnstile chega no
# desafio interativo mas nunca passa -- clique real via CDP fica preso em
# "Verifying..." e reseta. Suspeita confirmada: o Playwright puro vaza a
# própria conexão CDP (leak conhecido do `Runtime.enable`), não só flags de
# JS visíveis como `navigator.webdriver`. `patchright` é um fork mantido do
# Playwright que fecha esses leaks de CDP especificamente; Chrome real (não
# o Chromium open-source) reduz ainda mais a superfície de fingerprint.
RUN python -m patchright install chrome --with-deps

COPY oauth-browser/app.py ./app.py

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "xvfb-run --auto-servernum --server-args='-screen 0 1280x800x24' uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
