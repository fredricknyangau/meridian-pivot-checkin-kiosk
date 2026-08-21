from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
import asyncpg
from app.database.connection import get_db_pool
from app.schemas.checkin import (
    AttendeeSchema,
    CheckinResponse,
    AttendeeDetailResponse,
)
from app.services.checkin import CheckinService

router = APIRouter(tags=["checkin"])


def get_checkin_service(pool: asyncpg.Pool = Depends(get_db_pool)) -> CheckinService:
    return CheckinService(pool)


@router.post("/checkin/{attendee_id}", response_model=CheckinResponse)
async def checkin_attendee(
    attendee_id: int,
    service: CheckinService = Depends(get_checkin_service),
):
    result = await service.checkin_attendee(attendee_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attendee not found"
        )
    return result


@router.get("/attendees/{attendee_id}", response_model=AttendeeDetailResponse)
async def get_attendee(
    attendee_id: int,
    service: CheckinService = Depends(get_checkin_service),
):
    attendee = await service.get_attendee_detail(attendee_id)
    if attendee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attendee not found"
        )
    return attendee


@router.get("/checkin/attendees", response_model=List[AttendeeSchema])
async def list_attendees(
    service: CheckinService = Depends(get_checkin_service),
):
    return await service.get_all_attendees()
