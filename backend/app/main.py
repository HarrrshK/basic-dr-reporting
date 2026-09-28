import secrets

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from .api import analytics, backup, data_management, doctors, exports, imports, products, visits
from .config import settings
from .database import engine
from . import models  # noqa: F401


app = FastAPI(title="Field Visit Reporting API", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def protect_backup_operations(request, call_next):
    """Keep machine-to-machine backup routes private without blocking app users."""
    path = request.url.path
    protected = path.startswith("/api/backup/agent/") or path in {
        "/api/backup/export", "/api/backup/archive", "/api/backup/import",
    }
    if protected and request.method != "OPTIONS" and settings.api_access_token:
        supplied = request.headers.get("authorization", "")
        bearer = supplied.removeprefix("Bearer ")
        if not secrets.compare_digest(bearer, settings.api_access_token):
            return JSONResponse(status_code=401, content={"detail": "Backup agent authorization required."})
    return await call_next(request)


for router in (imports.router, doctors.router, visits.router, products.router, analytics.router,
               exports.router, data_management.router, backup.router):
    app.include_router(router, prefix="/api")


@app.exception_handler(OperationalError)
async def postgres_unavailable(_request, _error):
    return JSONResponse(status_code=503, content={
        "detail": "The production database is currently unavailable. Please retry shortly.",
        "database_available": False,
    })


@app.get("/api/health")
def health():
    return {"status": "ok"}
