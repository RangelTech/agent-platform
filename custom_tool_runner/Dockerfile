FROM python:3.12-slim

WORKDIR /srv
COPY custom_tool_runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY custom_tool_runner/app.py ./app.py

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
