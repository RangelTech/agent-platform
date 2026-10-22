# Motor de IA (API): sem frontend desde o U-6 (a UI e o Chatwoot).
FROM python:3.12-slim

WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations

ARG GIT_COMMIT=unknown
ARG SOURCE_URL=https://github.com/RangelTech/luar_engine
ENV PORT=8090 GIT_COMMIT=${GIT_COMMIT}
LABEL org.opencontainers.image.source=${SOURCE_URL}       org.opencontainers.image.revision=${GIT_COMMIT}

RUN useradd -r -u 10001 app
USER app

EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=2).status==200 else 1)"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
