from pydantic import BaseModel


class DisputeCreatePayload(BaseModel):
    review_id: str
    type: str
    reason: str
    target_org_id: str | None = None
