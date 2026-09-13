from fastapi import FastAPI

app = FastAPI(title="TopGreenCloud API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
