from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database.connection import init_db, close_db
from app.routes import checkin, webhook


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Solstice Events Check-In Kiosk",
    lifespan=lifespan
)

app.include_router(checkin.router)
app.include_router(webhook.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
