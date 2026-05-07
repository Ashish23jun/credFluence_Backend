from pydantic import BaseModel


class EvidencePresignRequest(BaseModel):
    content_type: str
    evidence_type: str


class RatingIn(BaseModel):
    category: str
    score: int


class PaymentIn(BaseModel):
    payment_type: str          # advance | milestone | final
    amount: int                # in smallest unit (paise)
    currency: str = "INR"
    status: str = "pending"    # pending | paid | late
    due_date: str | None = None
    paid_at: str | None = None


class FlagIn(BaseModel):
    type: str       # payment_not_made | payment_partial | payment_refused | payment_delayed | invoice_disputed | ghosted | missed_deadline | scope_creep | rude_behavior | contract_violation
    severity: str = "medium"  # low | medium | high


class EvidenceIn(BaseModel):
    type: str
    file_key: str


class OffPlatformTarget(BaseModel):
    name: str
    email: str
    kind: str           # creator | agency | brand
    youtube_url: str | None = None
    instagram_handle: str | None = None
    linkedin_url: str | None = None


class SubmitReviewRequest(BaseModel):
    # Exactly one of these must be set
    target_profile_handle: str | None = None
    off_platform: OffPlatformTarget | None = None

    body: str | None = None
    total_deal_value: int | None = None
    currency: str = "INR"
    contact_email: str
    contact_phone: str | None = None
    ratings: list[RatingIn] = []
    payments: list[PaymentIn] = []
    flags: list[FlagIn] = []
    tags: list[str] = []
    evidence: list[EvidenceIn] = []


class RecipientDisputePayload(BaseModel):
    reason: str
    evidence_keys: list[str] = []


class CommentIn(BaseModel):
    body: str


class ReplyIn(BaseModel):
    body: str
