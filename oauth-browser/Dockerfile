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
COPY oauth-browser/app.py ./app.py

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "xvfb-run --auto-servernum --server-args='-screen 0 1280x800x24' uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
