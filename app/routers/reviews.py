from fastapi import APIRouter

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("")
async def list_reviews() -> dict:
    return {"success": True, "message": "TODO: implement reviews", "data": []}
