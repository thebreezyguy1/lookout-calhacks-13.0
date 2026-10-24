from fastapi import FastAPI

app = FastAPI(
    title="Lookout",
    description="Scam-pattern detection for aging parents. Read-only; never moves money.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
