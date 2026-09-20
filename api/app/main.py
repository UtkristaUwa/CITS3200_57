from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import current_user

from app.config import settings
from app.routers import auth as auth_router, health, tenders

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
app.include_router(auth_router.router)
app.include_router(tenders.router, dependencies=[Depends(current_user)])
