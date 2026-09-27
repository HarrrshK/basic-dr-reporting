from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import analytics, data_management, doctors, exports, imports, products, visits
from .config import settings

app = FastAPI(title="Field Visit Reporting API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
for router in (imports.router, doctors.router, visits.router, products.router, analytics.router, exports.router, data_management.router):
    app.include_router(router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
