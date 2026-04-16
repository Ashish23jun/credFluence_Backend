from fastapi import APIRouter

router = APIRouter(prefix="/disputes", tags=["disputes"])


@router.get("")
async def list_disputes() -> dict:
    return {"success": True, "message": "TODO: implement disputes", "data": []}
