"""Dashboard API routes"""
from fastapi import APIRouter
from .stats import router as stats_router
from .conversations import router as conversations_router
from .reports import router as reports_router
from .trials import router as trials_router
from .criteria import router as criteria_router
from .analytics_simple import router as analytics_router
from .analytics_business_intelligence import router as bi_analytics_router

# Create main dashboard router
dashboard_router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    responses={404: {"description": "Not found"}}
)

# Include sub-routers
dashboard_router.include_router(stats_router)
dashboard_router.include_router(conversations_router)
dashboard_router.include_router(reports_router)
dashboard_router.include_router(trials_router)
dashboard_router.include_router(criteria_router)
dashboard_router.include_router(analytics_router)
dashboard_router.include_router(bi_analytics_router)