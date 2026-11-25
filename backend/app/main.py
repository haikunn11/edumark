from __future__ import annotations

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.app.core.config import settings
from backend.app.core.database import db
from backend.app.api.routes import router as api_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backbone API phục vụ Hệ thống Chấm bài Tự luận Viết tay Thông minh EduMark AI",
)

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=(origins != ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Đăng ký Router API v1
app.include_router(api_router, prefix=settings.API_V1_STR)

# Cấu hình Static Files cho Giao diện Web & Mobile PWA
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/health")
def root_health() -> dict:
    return {
        "status": "ok",
        "service": "EduMark AI",
        "version": settings.VERSION,
        "db_ok": db.ping(),
    }


@app.get("/")
def serve_index() -> FileResponse:
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return FileResponse(__file__)


@app.get("/app")
@app.get("/mobile")
def serve_mobile_app() -> FileResponse:
    mobile_file = os.path.join(static_dir, "mobile.html")
    if os.path.exists(mobile_file):
        return FileResponse(mobile_file)
    return serve_index()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
