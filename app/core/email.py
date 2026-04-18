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
