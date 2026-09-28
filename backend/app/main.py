from contextlib import asynccontextmanager
import secrets

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from .api import analytics, data_management, doctors, exports, imports, products, sync, visits
from .config import settings
from .database import Base, engine
from . import models  # noqa: F401
from .sync.runtime import queue_repository


@asynccontextmanager
async def lifespan(app: FastAPI):
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
    queue_repository.initialize()
    yield


app = FastAPI(title="Field Visit Reporting API", version="0.2.0", lifespan=lifespan)
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
                "detail": "Laptop API key required.", "auth_required": True,
            })
        # Seed the hosted SQLite workspace from the laptop's permanent database
        # before accepting any live-site changes. The agent endpoints are the
        # only writes permitted during this initial handshake.
        if (request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and request.url.path.startswith("/api/")
                and not request.url.path.startswith("/api/sync/agent/")
                and not queue_repository.bootstrap_complete()):
            return JSONResponse(status_code=409, content={
                "detail": "Start the laptop sync agent to initialize the hosted database before making changes.",
                "bootstrap_required": True,
            })
    return await call_next(request)


for router in (imports.router, doctors.router, visits.router, products.router, analytics.router, exports.router, data_management.router, sync.router):
    app.include_router(router, prefix="/api")


@app.exception_handler(OperationalError)
async def postgres_unavailable(_request, _error):
    return JSONResponse(status_code=503, content={
        "detail": "The application database is currently unavailable.",
        "database_available": False,
        "pending_records": queue_repository.pending_count(),
    })


@app.get("/api/health")
def health():
    return {"status": "ok"}
