"""
OG (Open Graph) router — server-rendered HTML shells + dynamic card images.

GET /og/profiles/{handle}         — profile card meta tags + redirect to SPA
GET /og/reviews/{review_id}       — review card meta tags + redirect to SPA
GET /og/image/profiles/{handle}   — 1200×630 PNG for og:image
"""

import io
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.profile import Profile
from app.models.review import Review, ReviewRating

router = APIRouter(prefix="/og", tags=["og"])

_SITE_NAME = "CredFluence"
_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_FONT_BOLD_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

_TYPE_COLOR: dict[str, tuple[int, int, int]] = {
    "creator": (139, 92, 246),   # purple
    "agency":  (59, 130, 246),   # blue
    "brand":   (245, 158, 11),   # amber
}
_BG_DARK = (13, 13, 15)
_FG = (240, 240, 245)
_FG_2 = (150, 150, 165)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _FONT_BOLD_PATH if bold else _FONT_PATH
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _trust_grade(score: int) -> str:
    if score >= 80: return "A+"
    if score >= 70: return "A"
    if score >= 60: return "B+"
    if score >= 50: return "B"
    return "C"


def _circle_avatar(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), mask=mask)
    return out


def _monogram_avatar(letter: str, size: int, color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (size, size), (*color, 255))
    draw = ImageDraw.Draw(img)
    font = _load_font(size // 2, bold=True)
    bbox = draw.textbbox((0, 0), letter, font=font)
    tx = (size - (bbox[2] - bbox[0])) // 2 - bbox[0]
    ty = (size - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((tx, ty), letter, fill=_FG, font=font)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


def _generate_og_image(
    display_name: str,
    profile_type: str,
    trust_score: int,
    niches: list[str],
    avatar_url: str | None,
) -> bytes:
    W, H = 1200, 630
    accent = _TYPE_COLOR.get(profile_type or "creator", _TYPE_COLOR["creator"])
    ar, ag, ab = accent

    # ── background: dark base + single soft accent rectangle (no scan lines) ─
    img = Image.new("RGB", (W, H), (10, 8, 18))
    # soft left glow via one large semi-transparent overlay
    glow = Image.new("RGB", (W, H), (ar // 8, ag // 8, ab // 8))
    mask = Image.new("L", (W, H), 0)
    glow_draw = ImageDraw.Draw(mask)
    # radial fade: bright centre-left, dark right — approximate with ellipse
    glow_draw.ellipse((-200, -100, 700, H + 100), fill=180)
    img = Image.composite(glow, img, mask)

    draw = ImageDraw.Draw(img)

    # top + bottom accent bars
    draw.rectangle([(0, 0), (W, 6)], fill=accent)
    draw.rectangle([(0, H - 6), (W, H)], fill=accent)

    # ── avatar (left side) ────────────────────────────────────────────────────
    AV = 260
    av_x = 80
    av_y = (H - AV) // 2

    # glow ring
    rp = 16
    draw.ellipse((av_x - rp, av_y - rp, av_x + AV + rp, av_y + AV + rp),
                 fill=(ar // 7, ag // 7, ab // 7))
    draw.ellipse((av_x - rp, av_y - rp, av_x + AV + rp, av_y + AV + rp),
                 outline=accent, width=3)

    avatar_img = None
    if avatar_url:
        try:
            with httpx.Client(timeout=4) as client:
                resp = client.get(avatar_url, follow_redirects=True)
            if resp.status_code == 200:
                avatar_img = _circle_avatar(Image.open(io.BytesIO(resp.content)), AV)
        except Exception:
            pass

    if avatar_img is None:
        letter = (display_name or "?")[0].upper()
        avatar_img = _monogram_avatar(letter, AV, accent)

    img.paste(avatar_img, (av_x, av_y), avatar_img)

    # vertical divider
    dx = av_x + AV + 60
    draw.line([(dx, 70), (dx, H - 70)], fill=(ar // 4, ag // 4, ab // 4), width=1)

    # ── right content (vertically centered block) ─────────────────────────────
    tx = dx + 64
    content_h = 46 + 20 + 100 + 24 + 96 + 20 + 36  # pill+gap+name+gap+score+gap+niche ≈ 342
    ty = (H - content_h) // 2

    # role pill
    role_label = (profile_type or "creator").upper()
    font_pill = _load_font(28)
    pp = 16
    bbox = draw.textbbox((0, 0), role_label, font=font_pill)
    pw = bbox[2] - bbox[0] + pp * 2
    ph = bbox[3] - bbox[1] + pp
    draw.rounded_rectangle([(tx, ty), (tx + pw, ty + ph)], radius=8,
                            fill=(ar // 7, ag // 7, ab // 7), outline=accent, width=2)
    draw.text((tx + pp, ty + pp // 2), role_label, fill=accent, font=font_pill)
    ty += ph + 22

    # display name — large and bold
    font_name = _load_font(86, bold=True)
    name = display_name or "Unknown"
    if len(name) > 16:
        name = name[:15] + "…"
    draw.text((tx, ty), name, fill=(240, 240, 250), font=font_name)
    name_bbox = draw.textbbox((0, 0), name, font=font_name)
    ty += (name_bbox[3] - name_bbox[1]) + 22

    # underline
    draw.line([(tx, ty), (tx + min(name_bbox[2] - name_bbox[0], 460), ty)],
              fill=(ar // 3, ag // 3, ab // 3), width=1)
    ty += 22

    # trust score — hero number
    font_score = _load_font(96, bold=True)
    font_grade = _load_font(48, bold=True)
    font_label = _load_font(26)
    grade = _trust_grade(trust_score)

    draw.text((tx, ty), "Trust Score", fill=(120, 120, 140), font=font_label)
    ty += 34
    score_str = str(trust_score)
    draw.text((tx, ty), score_str, fill=(240, 240, 250), font=font_score)
    sb = draw.textbbox((0, 0), score_str, font=font_score)
    draw.text((tx + sb[2] - sb[0] + 14, ty + 20), grade, fill=accent, font=font_grade)
    ty += (sb[3] - sb[1]) + 18

    # niches
    if niches:
        niche_str = "  ·  ".join(niches[:2])
        if len(niche_str) > 40:
            niche_str = niche_str[:39] + "…"
        draw.text((tx, ty), niche_str, fill=(110, 110, 130), font=_load_font(28))

    # ── branding ──────────────────────────────────────────────────────────────
    font_brand = _load_font(32, bold=True)
    bb = draw.textbbox((0, 0), _SITE_NAME, font=font_brand)
    draw.text((W - (bb[2] - bb[0]) - 52, H - 58), _SITE_NAME, fill=accent, font=font_brand)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _image_url(handle: str) -> str:
    return f"{settings.og_base_url}/og/image/profiles/{handle}"


def _html(title: str, description: str, image: str, redirect_url: str) -> str:
    s = lambda v: v.replace('"', "&quot;")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url={s(redirect_url)}">
  <title>{title}</title>
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="{_SITE_NAME}">
  <meta property="og:title" content="{s(title)}">
  <meta property="og:description" content="{s(description)}">
  <meta property="og:image" content="{s(image)}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:url" content="{s(redirect_url)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{s(title)}">
  <meta name="twitter:description" content="{s(description)}">
  <meta name="twitter:image" content="{s(image)}">
  <link rel="canonical" href="{s(redirect_url)}">
</head>
<body><a href="{s(redirect_url)}">Click here if not redirected</a></body>
</html>"""


# ── Image endpoint ────────────────────────────────────────────────────────────

@router.get("/image/profiles/{handle}", include_in_schema=False)
async def og_profile_image(handle: str, db: AsyncSession = Depends(get_db)):
    row = await db.execute(
        select(
            Profile.display_name,
            Profile.avatar_url,
            Profile.trust_score,
            Profile.niches,
            Profile.profile_type,
        ).where(Profile.handle == handle, Profile.is_opted_out.is_(False))
    )
    profile = row.one_or_none()

    if profile:
        png = _generate_og_image(
            display_name=profile.display_name or handle,
            profile_type=profile.profile_type or "creator",
            trust_score=profile.trust_score or 450,
            niches=profile.niches or [],
            avatar_url=profile.avatar_url,
        )
    else:
        png = _generate_og_image(
            display_name=handle,
            profile_type="creator",
            trust_score=450,
            niches=[],
            avatar_url=None,
        )

    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


# ── HTML meta endpoints ───────────────────────────────────────────────────────

@router.get("/profiles/{handle}", response_class=HTMLResponse, include_in_schema=False)
async def og_profile(handle: str, db: AsyncSession = Depends(get_db)):
    row = await db.execute(
        select(
            Profile.display_name,
            Profile.avatar_url,
            Profile.trust_score,
            Profile.niches,
            Profile.profile_type,
        ).where(Profile.handle == handle, Profile.is_opted_out.is_(False))
    )
    profile = row.one_or_none()

    redirect = f"{settings.frontend_url}/profiles/{handle}"
    image = _image_url(handle)

    if not profile:
        return HTMLResponse(_html(
            title=f"@{handle} on CredFluence — Creator & Brand Trust Profiles",
            description=f"See verified reviews, trust scores, and collaboration history for @{handle} on CredFluence — India's creator trust platform.",
            image=image,
            redirect_url=redirect,
        ))

    display_name = profile.display_name or f"@{handle}"
    score = profile.trust_score or 450
    grade = _trust_grade(score)
    niches: list[str] = profile.niches or []
    niche_str = " · ".join(niches[:2]) if niches else ""
    role = (profile.profile_type or "creator").capitalize()

    title = f"{display_name} — {role} Profile | CredFluence Trust Score {score} ({grade})"
    if len(title) > 60:
        title = f"{display_name} | CredFluence — Trust Score {score} · {role}"

    desc_parts = [f"Verified {role.lower()} on CredFluence with a Trust Score of {score}/{90}."]
    if niche_str:
        desc_parts.append(f"Specialises in {niche_str}.")
    desc_parts.append("See reviews, disputes, and collaboration history.")
    description = " ".join(desc_parts)

    return HTMLResponse(_html(title=title, description=description, image=image, redirect_url=redirect))


@router.get("/reviews/{review_id}", response_class=HTMLResponse, include_in_schema=False)
async def og_review(review_id: str, db: AsyncSession = Depends(get_db)):
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        return HTMLResponse(_html(
            title=f"Verified Review | CredFluence — Creator & Brand Trust Platform",
            description="Read verified reviews from creators, agencies, and brands on CredFluence — India's trust platform for the creator economy.",
            image=_image_url(""),
            redirect_url=settings.frontend_url,
        ))

    row = await db.execute(
        select(
            Review.body,
            Review.status,
            Profile.display_name,
            Profile.handle,
            Profile.profile_type,
            func.avg(ReviewRating.score).label("avg_score"),
        )
        .join(Profile, Profile.id == Review.target_profile_id)
        .outerjoin(ReviewRating, ReviewRating.review_id == Review.id)
        .where(Review.id == rid)
        .group_by(Review.id, Profile.display_name, Profile.handle, Profile.profile_type)
    )
    review = row.one_or_none()

    if not review or review.status not in ("verified", "in_window"):
        return HTMLResponse(_html(
            title=f"Verified Review | CredFluence — Creator & Brand Trust Platform",
            description="Read verified reviews from creators, agencies, and brands on CredFluence — India's trust platform for the creator economy.",
            image=_image_url(""),
            redirect_url=settings.frontend_url,
        ))

    handle = review.handle or ""
    redirect = f"{settings.frontend_url}/profiles/{handle}"
    profile_name = review.display_name or handle
    role = (review.profile_type or "creator").capitalize()
    avg = round(float(review.avg_score), 1) if review.avg_score else None
    stars = f"{'★' * round(avg)}{'☆' * (5 - round(avg))}" if avg else "★★★★★"
    body_snippet = (review.body or "")[:100]
    if len(review.body or "") > 100:
        body_snippet += "…"

    title = f"{stars} Review of {profile_name} ({role}) | CredFluence"
    description = (
        f"{body_snippet} — Verified review on CredFluence, India's trust platform for the creator economy."
        if body_snippet
        else f"Verified {role.lower()} review on CredFluence — India's trust platform for creators, agencies, and brands."
    )

    return HTMLResponse(_html(title=title, description=description, image=_image_url(handle), redirect_url=redirect))
