from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import close_pools, open_pools
from .routes import ask, boards, cards


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pools()
    yield
    close_pools()


app = FastAPI(title="Semantic Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(boards.router)
app.include_router(cards.router)
app.include_router(ask.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
