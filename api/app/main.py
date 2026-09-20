from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import health, tenders

app = FastAPI(title="TenderAI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tenderai-dev-f0283.web.app",
        "https://tenderai-dev-f0283.firebaseapp.com",
        "http://localhost:5173",
        *settings.allowed_origins_list,
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(tenders.router)
