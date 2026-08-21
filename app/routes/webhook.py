import logging
from fastapi import APIRouter, Depends, HTTPException, status
import asyncpg
from app.database.connection import get_db_pool
from app.schemas.checkin import (
    PrintConfirmationPayload,
    PrintConfirmationResponse,
)
from app.services.checkin import CheckinService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


def get_checkin_service(pool: asyncpg.Pool = Depends(get_db_pool)) -> CheckinService:
    return CheckinService(pool)


@router.post("/print-confirmation", response_model=PrintConfirmationResponse)
async def print_confirmation_webhook(
    payload: PrintConfirmationPayload,
    service: CheckinService = Depends(get_checkin_service),
):
    result = await service.process_print_confirmation(payload.print_job_id, payload.result)
    if result is None:
        logger.warning("Received webhook for unknown print_job_id: %s", payload.print_job_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Print job not found"
        )
    return result


@router.get("/health")
async def webhook_health():
    return {"status": "ok", "service": "webhook"}
