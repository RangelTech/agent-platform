from fastapi import FastAPI

app = FastAPI(title="agent-platform kernel")


@app.get("/health")
def health():
    return {"status": "ok", "service": "kernel"}
