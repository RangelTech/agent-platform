# Stage 1: build the SPA
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ .
RUN npm run build

# Stage 2: backend + static build
FROM python:3.12-slim

WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY --from=frontend /build/dist ./static

ENV PORT=8090 STATIC_DIR=/srv/static
EXPOSE 8090
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
