from fastapi import APIRouter

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("")
async def list_profiles() -> dict:
    return {"success": True, "message": "TODO: implement profiles", "data": []}
