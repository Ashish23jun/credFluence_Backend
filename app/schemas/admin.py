from pydantic import BaseModel


class OrgRejectPayload(BaseModel):
    reason: str


class ReviewRejectPayload(BaseModel):
    reason: str


class DisputeResolvePayload(BaseModel):
    outcome: str           # reviewer_won | target_won | mutual_resolution
    resolution_notes: str = ""
