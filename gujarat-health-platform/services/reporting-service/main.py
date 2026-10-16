"""
reporting-service — POC stub
FRD reference: FRD v4 Section 5.7
MIS dashboards, CVC disclosure, RTI flagging

This is a scaffold, not production logic: each endpoint returns a realistic
demo payload so the platform can be wired end-to-end and shown to customers.
Replace the TODOs with real business logic as the pilot matures.
"""
import os
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reporting-service")

SERVICE_NAME = "reporting-service"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/reporting_db")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

app = FastAPI(title="reporting-service", description="MIS dashboards, CVC disclosure, RTI flagging")


@app.get("/health")
def health():
    """Liveness/readiness probe used by docker-compose and, later, Kubernetes."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "frd_reference": "FRD v4 Section 5.7",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/dashboards/tender-summary")
def dashboards_tender_summary_get():
    """TODO: demo tender-summary dashboard data"""
    # Demo stub — wire this up to DATABASE_URL / downstream services as the
    # pilot matures. Returning a fixed shape now keeps the frontend and other
    # services able to integrate against this endpoint immediately.
    return {"service": SERVICE_NAME, "endpoint": "/dashboards/tender-summary", "detail": "demo tender-summary dashboard data", "demo": True}


@app.get("/disclosures/monthly")
def disclosures_monthly_get():
    """TODO: mock monthly CVC disclosure"""
    # Demo stub — wire this up to DATABASE_URL / downstream services as the
    # pilot matures. Returning a fixed shape now keeps the frontend and other
    # services able to integrate against this endpoint immediately.
    return {"service": SERVICE_NAME, "endpoint": "/disclosures/monthly", "detail": "mock monthly CVC disclosure", "demo": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8012)
