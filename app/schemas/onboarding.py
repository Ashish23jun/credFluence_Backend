import re

from pydantic import BaseModel, field_validator, model_validator

_GST_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


class SocialLinkItem(BaseModel):
    platform: str   # instagram | youtube | linkedin | twitter | facebook | tiktok
    url: str
    label: str | None = None  # optional display label e.g. "Main Page", "India"


class OrgUpdatePayload(BaseModel):
    name: str | None = None
    bio: str | None = None
    category: str | None = None
    location: str | None = None
    avatar_url: str | None = None
    languages: list[str] | None = None
    niches: list[str] | None = None
    social_links: list[SocialLinkItem] | None = None  # agency/brand only


class VerificationDocsPayload(BaseModel):
    website: str
    gst_number: str | None = None
    gst_file_url: str | None = None
    cin_number: str | None = None
    cin_file_url: str | None = None
    trademark_file_url: str | None = None

    @field_validator("gst_number")
    @classmethod
    def validate_gst(cls, v: str | None) -> str | None:
        if v and not _GST_RE.match(v.strip().upper()):
            raise ValueError("Invalid GST number format (e.g. 27ABCDE1234F1Z5)")
        return v.strip().upper() if v else v

    @model_validator(mode="after")
    def gst_required(self) -> "VerificationDocsPayload":
        if not self.gst_number and not self.gst_file_url:
            raise ValueError("GST number or GST certificate is required")
        return self


class PresignRequest(BaseModel):
    filename: str
    content_type: str
