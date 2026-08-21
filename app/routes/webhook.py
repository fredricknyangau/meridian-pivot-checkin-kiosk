from fastapi import APIRouter

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.get("/health")
async def webhook_health():
    return {"status": "ok", "service": "webhook"}
