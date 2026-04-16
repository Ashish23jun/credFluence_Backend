from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications() -> dict:
    return {"success": True, "message": "TODO: implement notifications", "data": []}
