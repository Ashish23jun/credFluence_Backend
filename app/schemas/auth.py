from pydantic import BaseModel, EmailStr, field_validator, model_validator

# ---------------------------------------------------------------------------
# Domain blocklist — only applies to agency/brand, NOT creators
# ---------------------------------------------------------------------------

BLOCKED_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "protonmail.com", "zoho.com",
    "yopmail.com", "mailinator.com", "guerrillamail.com", "temp-mail.org",
}


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str
    role: str  # creator | agency | brand

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v: str, info: object) -> str:
        data = getattr(info, "data", {})
        if "password" in data and v != data["password"]:
            raise ValueError("Passwords do not match")
        return v

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ("creator", "agency", "brand"):
            raise ValueError("Role must be one of: creator, agency, brand")
        return v

    @model_validator(mode="after")
    def business_email_for_non_creators(self) -> "RegisterRequest":
        # Creators are individuals — personal email domains are allowed
        if self.role in ("agency", "brand"):
            domain = self.email.split("@")[-1].lower()
            if domain in BLOCKED_EMAIL_DOMAINS:
                raise ValueError(
                    "Agencies and brands must use a business email address. "
                    "Free email providers (Gmail, Yahoo, etc.) are not allowed."
                )
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    otp: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


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
