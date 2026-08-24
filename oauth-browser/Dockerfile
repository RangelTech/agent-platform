# Imagem oficial já traz Chromium + todas as deps do sistema pra rodar --
# montar isso na mão com apt-get é frágil e muda a cada versão do Chromium.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /srv
COPY oauth-browser/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY oauth-browser/app.py ./app.py

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
