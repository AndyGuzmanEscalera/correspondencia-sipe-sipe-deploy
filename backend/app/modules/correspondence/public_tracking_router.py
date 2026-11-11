from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.correspondence.public_tracking_service import (
    NOT_FOUND, PublicTrackingRequest, PublicTrackingResponse,
    PublicTrackingService, TrackingRateLimiter,
)

limiter = TrackingRateLimiter()


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            limiter.check(request.client.host if request.client else "unknown")
            try:
                return await original(request)
            except RequestValidationError:
                # Default 422 includes raw input; never disclose applicant CI.
                return JSONResponse(NOT_FOUND, headers={"Cache-Control": "no-store"})
        return handler


router = APIRouter(prefix="/public/correspondence", tags=["public tracking"], route_class=PrivateValidationRoute)


@router.post("/track", response_model=PublicTrackingResponse, response_model_exclude_none=True)
def track_route_sheet(body: PublicTrackingRequest, db: Session = Depends(get_db)):
    return JSONResponse(
        PublicTrackingResponse.model_validate(PublicTrackingService(db).track(body)).model_dump(exclude_none=True),
        headers={"Cache-Control": "no-store"},
    )
