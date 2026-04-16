from pydantic import BaseModel, EmailStr, field_validator

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

BLOCKED_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "protonmail.com", "zoho.com",
    "yopmail.com", "mailinator.com", "guerrillamail.com", "temp-mail.org",
}


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: str  # creator | agency | brand

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ("creator", "agency", "brand"):
            raise ValueError("Role must be one of: creator, agency, brand")
        return v

    @field_validator("email")
    @classmethod
    def business_email_only(cls, v: str) -> str:
        domain = v.split("@")[-1].lower()
        if domain in BLOCKED_EMAIL_DOMAINS:
            raise ValueError(
                "Please use a business email address. "
                "Free email providers (Gmail, Yahoo, etc.) are not allowed."
            )
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    is_verified: bool
    subscription_tier: str


class AuthResponse(BaseModel):
    success: bool = True
    message: str
    data: dict
