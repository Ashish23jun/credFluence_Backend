import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_email(to_email: str, subject: str, html_body: str, text_body: str = "") -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.email_from_name} <{settings.email_from}>"
    msg["To"] = to_email

    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        kwargs: dict = {
            "hostname": settings.smtp_host,
            "port": settings.smtp_port,
        }

        # Port 465 = implicit SSL; port 587 = STARTTLS
        if settings.smtp_port == 465:
            kwargs["use_tls"] = True
        elif settings.smtp_tls:
            kwargs["start_tls"] = True

        if settings.smtp_user:
            kwargs["username"] = settings.smtp_user
        if settings.smtp_password:
            kwargs["password"] = settings.smtp_password

        await aiosmtplib.send(msg, **kwargs)
        logger.info("Email sent to %s — subject: %s", to_email, subject)

    except Exception as e:
        logger.warning("SMTP send failed: %s", e)
        # Dev fallback — log OTP to console so testing still works
        logger.info("[DEV EMAIL] To: %s | Subject: %s\n%s", to_email, subject, text_body or html_body)


async def send_otp_email(to_email: str, otp: str) -> None:
    text = f"Your CredFluence verification code is: {otp}\n\nIt expires in 10 minutes."
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">
      <h2 style="color:#111;margin-bottom:8px;">Verify your email</h2>
      <p style="color:#555;margin-bottom:24px;">
        Use the code below to complete your CredFluence registration.
        It expires in <strong>10 minutes</strong>.
      </p>
      <div style="background:#f4f4f5;border-radius:8px;padding:24px;text-align:center;margin-bottom:24px;">
        <span style="font-size:36px;font-weight:700;letter-spacing:10px;color:#111;">{otp}</span>
      </div>
      <p style="color:#888;font-size:13px;">
        If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    """
    await send_email(to_email, f"Your CredFluence verification code: {otp}", html, text)


# ──────────────────────────────────────────────────────────────────────────────
# Review lifecycle email templates
# ──────────────────────────────────────────────────────────────────────────────

def _wrap(title: str, intro: str, body_html: str, cta_url: str | None = None, cta_label: str | None = None, footer: str | None = None) -> str:
    cta = ""
    if cta_url and cta_label:
        cta = f"""
      <div style="text-align:center;margin:32px 0 8px;">
        <a href="{cta_url}" style="display:inline-block;background:#E8FF5B;color:#111;text-decoration:none;padding:13px 28px;border-radius:999px;font-weight:600;font-size:14px;letter-spacing:0.01em;">{cta_label}</a>
      </div>
        """
    foot = footer or "If this wasn't you or you have questions, reply to this email and our team will help."
    return f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif;background:#0B0B0C;padding:40px 16px;">
      <div style="max-width:560px;margin:0 auto;background:#16161A;border:1px solid rgba(255,255,255,0.09);border-radius:16px;overflow:hidden;">
        <div style="padding:24px 32px;border-bottom:1px solid rgba(255,255,255,0.09);">
          <span style="font-family:'Fraunces',serif;font-size:20px;color:#F5F1EA;font-weight:400;letter-spacing:-0.01em;">Cred<em style="color:#E8FF5B;font-style:italic;">Fluence</em></span>
        </div>
        <div style="padding:32px;">
          <h2 style="font-family:'Fraunces',serif;color:#F5F1EA;font-weight:300;font-size:26px;letter-spacing:-0.02em;margin:0 0 12px;">{title}</h2>
          <p style="color:rgba(245,241,234,0.72);font-size:14px;line-height:1.6;margin:0 0 16px;">{intro}</p>
          {body_html}
          {cta}
          <p style="color:rgba(245,241,234,0.5);font-size:12px;line-height:1.6;margin:24px 0 0;border-top:1px solid rgba(255,255,255,0.09);padding-top:16px;">{foot}</p>
        </div>
      </div>
      <p style="text-align:center;color:rgba(245,241,234,0.32);font-size:11px;margin-top:16px;font-family:monospace;letter-spacing:0.05em;">CREDFLUENCE · TRUST FOR CREATORS, AGENCIES &amp; BRANDS</p>
    </div>
    """


def _review_detail_html(snapshot: dict) -> str:
    """Renders a full review snapshot as an HTML block for use inside email templates."""
    s = snapshot
    rows = []

    # ── Relationship ──────────────────────────────────────────────────────────
    if s.get("relationship"):
        rows.append(f"""
          <tr>
            <td style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.07);color:rgba(245,241,234,0.45);font-size:12px;width:40%;">Relationship</td>
            <td style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.07);color:#F5F1EA;font-size:13px;">{s['relationship']}</td>
          </tr>""")

    # ── Deal value ────────────────────────────────────────────────────────────
    if s.get("total_deal_value"):
        rupees = f"₹{s['total_deal_value'] / 100:,.0f}"
        rows.append(f"""
          <tr>
            <td style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.07);color:rgba(245,241,234,0.45);font-size:12px;">Deal value</td>
            <td style="padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.07);color:#F5F1EA;font-size:13px;">{rupees}</td>
          </tr>""")

    table_html = f"""
      <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
        {''.join(rows)}
      </table>""" if rows else ""

    # ── Written comment ───────────────────────────────────────────────────────
    comment_html = ""
    if s.get("body"):
        comment_html = f"""
      <div style="background:#1C1C21;border-left:3px solid #E8FF5B;border-radius:4px;padding:14px 18px;margin-bottom:20px;">
        <div style="font-size:11px;color:rgba(245,241,234,0.45);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px;font-family:monospace;">Written comment</div>
        <p style="color:rgba(245,241,234,0.85);font-size:14px;line-height:1.7;margin:0;">{s['body']}</p>
      </div>"""

    # ── Ratings ───────────────────────────────────────────────────────────────
    ratings_html = ""
    if s.get("ratings"):
        stars = lambda n: "★" * n + "☆" * (5 - n)
        rating_rows = "".join(
            f"""<tr>
              <td style="padding:7px 0;color:rgba(245,241,234,0.6);font-size:12px;width:55%;">{r['category']}</td>
              <td style="padding:7px 0;color:#E8FF5B;font-family:monospace;font-size:14px;letter-spacing:2px;">{stars(r['score'])}</td>
              <td style="padding:7px 0;color:rgba(245,241,234,0.4);font-size:12px;text-align:right;">{r['score']}/5</td>
            </tr>"""
            for r in s["ratings"]
        )
        ratings_html = f"""
      <div style="margin-bottom:20px;">
        <div style="font-size:11px;color:rgba(245,241,234,0.45);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;font-family:monospace;">Ratings</div>
        <table style="width:100%;border-collapse:collapse;">{rating_rows}</table>
      </div>"""

    # ── Payments ──────────────────────────────────────────────────────────────
    payments_html = ""
    if s.get("payments"):
        STATUS_COLOR = {"paid": "#8DFFB8", "late": "#FF6464", "pending": "#E8FF5B"}
        payment_chips = "".join(
            f"""<div style="display:inline-block;background:#1C1C21;border:1px solid rgba(255,255,255,0.09);border-radius:8px;padding:10px 14px;margin:0 8px 8px 0;min-width:120px;">
              <div style="font-size:10px;color:rgba(245,241,234,0.45);text-transform:uppercase;letter-spacing:0.08em;font-family:monospace;margin-bottom:4px;">{p['type']}</div>
              <div style="font-size:15px;font-weight:600;color:#F5F1EA;margin-bottom:3px;">₹{p['amount_rupees']:,.0f}</div>
              <div style="font-size:11px;color:{STATUS_COLOR.get(p['status'], '#F5F1EA')};">{p['status'].upper()}</div>
            </div>"""
            for p in s["payments"]
        )
        payments_html = f"""
      <div style="margin-bottom:20px;">
        <div style="font-size:11px;color:rgba(245,241,234,0.45);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;font-family:monospace;">Payments</div>
        {payment_chips}
      </div>"""

    # ── Flags ─────────────────────────────────────────────────────────────────
    flags_html = ""
    if s.get("flags"):
        SEV_COLOR = {"high": "#FF6464", "medium": "#FFB347", "low": "#E8FF5B"}
        flag_items = "".join(
            f"""<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;background:#1C1C21;border:1px solid rgba(255,100,100,0.2);border-radius:8px;margin-bottom:6px;">
              <span style="font-size:13px;">⚑</span>
              <span style="font-size:13px;color:#F5F1EA;flex:1;">{f['type']}</span>
              <span style="font-size:10px;font-family:monospace;color:{SEV_COLOR.get(f['severity'], '#F5F1EA')};text-transform:uppercase;">{f['severity']}</span>
            </div>"""
            for f in s["flags"]
        )
        flags_html = f"""
      <div style="margin-bottom:20px;">
        <div style="font-size:11px;color:rgba(245,241,234,0.45);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;font-family:monospace;">Issues flagged</div>
        {flag_items}
      </div>"""

    # ── Tags ──────────────────────────────────────────────────────────────────
    tags_html = ""
    if s.get("tags"):
        tag_chips = "".join(
            f'<span style="display:inline-block;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:4px;padding:3px 10px;font-size:11px;color:rgba(245,241,234,0.6);font-family:monospace;margin:0 5px 5px 0;letter-spacing:0.05em;">{t}</span>'
            for t in s["tags"]
        )
        tags_html = f"""
      <div style="margin-bottom:16px;">
        <div style="font-size:11px;color:rgba(245,241,234,0.45);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:10px;font-family:monospace;">Tags</div>
        {tag_chips}
      </div>"""

    # ── Evidence ──────────────────────────────────────────────────────────────
    evidence_html = ""
    if s.get("evidence_count", 0) > 0:
        evidence_html = f"""
      <p style="color:rgba(245,241,234,0.5);font-size:12px;margin:0 0 16px;">
        📎 {s['evidence_count']} supporting document{'s' if s['evidence_count'] != 1 else ''} attached (visible after logging in)
      </p>"""

    return table_html + comment_html + ratings_html + payments_html + flags_html + tags_html + evidence_html


async def send_review_received_email(to_email: str, reviewer_name: str, review_id: str, snapshot: dict) -> None:
    """Sent to all org admins when a review is submitted for their org."""
    url = f"{settings.frontend_url}/notifications?review_id={review_id}"
    subject = f"New review from {reviewer_name} — CredFluence"
    detail = _review_detail_html(snapshot)
    html = _wrap(
        title="A new review has arrived",
        intro=f"<strong style='color:#F5F1EA;'>{reviewer_name}</strong> just left a review for your organisation. You have <strong>48 hours</strong> to raise a dispute before it goes live.",
        body_html=detail,
        cta_url=url,
        cta_label="View Review & Respond",
    )
    text = f"{reviewer_name} left a review for your org. View it: {url}"
    await send_email(to_email, subject, html, text)


async def send_review_received_invite_email(to_email: str, reviewer_name: str, review_id: str, claim_url: str, snapshot: dict) -> None:
    """Sent to off-platform target — includes full review so they know what was written."""
    subject = f"{reviewer_name} submitted a review about your organisation — CredFluence"
    detail = _review_detail_html(snapshot)
    intro_blurb = f"""
      <p style="color:rgba(245,241,234,0.72);font-size:14px;line-height:1.6;margin:0 0 20px;">
        <strong style="color:#F5F1EA;">{reviewer_name}</strong> submitted a review about your organisation on CredFluence —
        a verified trust platform for creators, agencies and brands in India.
        Claim your profile to respond or dispute it. The 48-hour window starts only after you claim.
      </p>"""
    html = _wrap(
        title="You've been reviewed on CredFluence",
        intro=f"Here's what {reviewer_name} wrote about you.",
        body_html=intro_blurb + detail,
        cta_url=claim_url,
        cta_label="Claim Your Profile & Respond",
    )
    text = f"{reviewer_name} submitted a review about your org. Claim your profile: {claim_url}"
    await send_email(to_email, subject, html, text)


async def send_platform_admin_alert_email(to_email: str, target_name: str, contact_email: str, review_id: str) -> None:
    """Sent to all platform admins when a review is submitted for an off-platform org."""
    url = f"{settings.frontend_url}/admin/reviews/{review_id}"
    subject = f"[Admin] New off-platform review for {target_name}"
    body = f"""
      <div style="background:#1C1C21;border:1px solid rgba(255,255,255,0.09);border-radius:10px;padding:16px;font-family:monospace;font-size:13px;color:rgba(245,241,234,0.72);">
        <div><span style="color:rgba(245,241,234,0.5);">Target:</span> {target_name}</div>
        <div><span style="color:rgba(245,241,234,0.5);">Contact:</span> {contact_email}</div>
        <div><span style="color:rgba(245,241,234,0.5);">Review ID:</span> {review_id}</div>
      </div>
    """
    html = _wrap(
        title="Off-platform review needs review",
        intro="A user submitted a review for an organisation that is not yet on CredFluence. Decide whether to invite, approve directly, or reject.",
        body_html=body,
        cta_url=url,
        cta_label="Open Admin Console",
        footer="This is an internal notification for platform admins.",
    )
    text = f"Off-platform review for {target_name} ({contact_email}). Review ID: {review_id}. {url}"
    await send_email(to_email, subject, html, text)


async def send_review_live_email(to_email: str, target_name: str, review_id: str, role: str) -> None:
    """Sent to target org admins + reviewer when the 48hr window expires and review goes live."""
    url = f"{settings.frontend_url}/notifications?review_id={review_id}"
    if role == "target":
        intro = "The 48-hour dispute window has closed. The review is now publicly visible on your profile."
    else:
        intro = "The 48-hour dispute window has closed and the review you submitted is now publicly visible."
    body = f"""
      <p style="color:rgba(245,241,234,0.72);font-size:14px;line-height:1.6;margin:0;">
        Review for <strong style="color:#F5F1EA;">{target_name}</strong> has gone live.
      </p>
    """
    html = _wrap(title="Your review is now live", intro=intro, body_html=body, cta_url=url, cta_label="View Review")
    text = f"Review for {target_name} is now live: {url}"
    await send_email(to_email, "Your CredFluence review is now live", html, text)


async def send_dispute_filed_email(to_email: str, case_id: str, review_id: str, role: str) -> None:
    """Sent to both reviewer and target when a dispute is filed."""
    url = f"{settings.frontend_url}/notifications?review_id={review_id}"
    if role == "reviewer":
        title = "A dispute has been filed on your review"
        intro = f"The target organisation has disputed the review you submitted. Case ID <strong style='color:#E8FF5B;'>{case_id}</strong>."
    else:
        title = "Your dispute has been received"
        intro = f"We've received your dispute. Our platform admins will mediate and update both parties. Case ID <strong style='color:#E8FF5B;'>{case_id}</strong>."
    body = f"""
      <div style="background:#1C1C21;border:1px solid rgba(255,255,255,0.09);border-radius:10px;padding:14px 18px;text-align:center;">
        <div style="font-family:monospace;font-size:11px;color:rgba(245,241,234,0.5);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">Case ID</div>
        <div style="font-family:monospace;font-size:18px;color:#E8FF5B;font-weight:600;letter-spacing:0.05em;">{case_id}</div>
      </div>
    """
    html = _wrap(title=title, intro=intro, body_html=body, cta_url=url, cta_label="View Case")
    text = f"Dispute filed. Case ID: {case_id}. View: {url}"
    await send_email(to_email, f"Dispute filed — Case {case_id}", html, text)


async def send_password_reset_email(to_email: str, otp: str) -> None:
    text = f"Your CredFluence password reset code is: {otp}\n\nIt expires in 10 minutes."
    html = _wrap(
        title="Reset your password",
        intro="Use the code below to reset your CredFluence password. It expires in <strong>10 minutes</strong>.",
        body_html=f"""
      <div style="background:#1C1C21;border-radius:10px;padding:24px;text-align:center;margin-bottom:20px;">
        <div style="font-size:11px;color:rgba(245,241,234,0.45);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:12px;font-family:monospace;">Reset code</div>
        <span style="font-size:36px;font-weight:700;letter-spacing:10px;color:#E8FF5B;font-family:monospace;">{otp}</span>
      </div>""",
        footer="If you didn't request a password reset, you can safely ignore this email. Your password won't change.",
    )
    await send_email(to_email, f"Reset your CredFluence password — code: {otp}", html, text)


async def send_account_deletion_email(to_email: str, days_remaining: int = 30) -> None:
    html = _wrap(
        title="Account deletion scheduled",
        intro="Your CredFluence account has been scheduled for deletion.",
        body_html=f"""
      <div style="background:#1C1C21;border:1px solid rgba(255,100,100,0.2);border-radius:10px;padding:20px;margin-bottom:20px;">
        <p style="color:rgba(245,241,234,0.72);font-size:14px;line-height:1.6;margin:0 0 12px;">
          Your account and all associated data will be <strong style="color:#FF6464;">permanently deleted</strong> in <strong style="color:#F5F1EA;">{days_remaining} days</strong>.
        </p>
        <p style="color:rgba(245,241,234,0.72);font-size:14px;line-height:1.6;margin:0;">
          Changed your mind? Simply <strong style="color:#E8FF5B;">log in again</strong> within {days_remaining} days and your account will be fully restored.
        </p>
      </div>""",
        footer="If you didn't request this, log in immediately to cancel the deletion.",
    )
    text = f"Your CredFluence account is scheduled for deletion in {days_remaining} days. Log in to cancel."
    await send_email(to_email, "Your CredFluence account is scheduled for deletion", html, text)


async def send_dispute_resolved_email(to_email: str, case_id: str, outcome: str) -> None:
    """Sent to both reviewer and target when admin resolves a dispute."""
    url = f"{settings.frontend_url}/notifications"
    outcome_msg = {
        "reviewer_won": "The platform admin sided with the reviewer. The review will go live publicly.",
        "target_won": "The platform admin sided with the target. The review has been hidden.",
        "mutual_resolution": "Both parties reached a resolution. The review will go live publicly.",
    }.get(outcome, "Your dispute has been resolved.")

    body = f"""
      <div style="background:#1C1C21;border:1px solid rgba(255,255,255,0.09);border-radius:10px;padding:14px 18px;text-align:center;margin-bottom:16px;">
        <div style="font-family:monospace;font-size:11px;color:rgba(245,241,234,0.5);letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">Case ID</div>
        <div style="font-family:monospace;font-size:18px;color:#E8FF5B;font-weight:600;letter-spacing:0.05em;">{case_id}</div>
      </div>
      <p style="color:rgba(245,241,234,0.72);font-size:14px;line-height:1.6;margin:0;">{outcome_msg}</p>
    """
    html = _wrap(
        title="Your dispute has been resolved",
        intro=f"Your dispute (case <strong style='color:#E8FF5B;'>{case_id}</strong>) has been reviewed by a platform admin.",
        body_html=body,
        cta_url=url,
        cta_label="View Resolution",
    )
    text = f"Dispute {case_id} resolved. Outcome: {outcome}. {url}"
    await send_email(to_email, f"Dispute resolved — Case {case_id}", html, text)
