"""Router aggregation without application globals."""

from income_stats.handlers.admin_handler import admin_router
from income_stats.handlers.analytics_handler import analytics_router
from income_stats.handlers.income_handler import income_router
from income_stats.handlers.records_handler import records_router

routers = (admin_router, analytics_router, records_router, income_router)

__all__ = [
    "admin_router",
    "analytics_router",
    "income_router",
    "records_router",
    "routers",
]
