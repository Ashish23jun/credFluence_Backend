from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("")
async def list_admin() -> dict:
    return {"success": True, "message": "TODO: implement admin", "data": []}
