from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from apps.api.controllers import stats as controller
from apps.api.services.stats_service import StatsService

router = APIRouter(prefix="/v1/stats", tags=["stats"])


def get_service(request: Request) -> StatsService:
    return request.app.state.stats_service  # type: ignore[no-any-return]


Service = Annotated[StatsService, Depends(get_service)]


@router.get("")
def get_stats(service: Service, days: int = 7) -> dict[str, Any]:
    return controller.get_stats(service, days)
