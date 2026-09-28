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
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def require_api_access_token(request, call_next):
    if (request.url.path.startswith("/api/") and request.url.path != "/api/health"
            and request.method != "OPTIONS"):
        configured = settings.api_access_token
        if not configured:
            return JSONResponse(status_code=503, content={
                "detail": "Set API_ACCESS_TOKEN on the backend before connecting a frontend.",
                "auth_required": True,
            })
        supplied = request.headers.get("authorization", "")
        bearer = supplied.removeprefix("Bearer ")
        if not secrets.compare_digest(bearer, configured):
            return JSONResponse(status_code=401, content={
                "detail": "API access key required.", "auth_required": True,
            })
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
