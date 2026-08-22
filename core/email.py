import asyncio
import logging
import html
import resend

from core.config import settings

resend.api_key = settings.RESEND_API_KEY

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when email delivery fails via Resend."""


async def send_verification_email(email: str, code: str) -> None:
    """Send the account verification code via Resend."""
    if not resend.api_key:
        logger.warning("Resend API key is not configured; verification email was not sent")
        return

    escaped_code = html.escape(code)

    try:
        response = await resend.Emails.send_async({
            "from": f"LinkeFlow <{settings.FROM_EMAIL}>",
            "to": [email],
            "subject": "Verify your Link Easy account",
            "html": f"""
                <div style="font-family: Arial, sans-serif; max-width: 480px; margin: auto;">
                    <h2>Welcome to Linkeflow!</h2>
                    <p>Use the code below to verify your email address:</p>
                    <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; background: #f4f4f4; padding: 16px; text-align: center; border-radius: 8px; margin: 24px 0;">
                        {escaped_code}
                    </div>
                    <p style="color: #888; font-size: 13px;">This code expires in 15 minutes. If you did not sign up, please ignore this email.</p>
                </div>
            """
        })
        logger.info(f"Verification email sent successfully. ID: {response.id}")
    except Exception as e:
        logger.error(f"Failed to send verification email via Resend: {str(e)}")
        raise EmailDeliveryError(f"Could not send verification email: {str(e)}") from e


async def send_password_reset_email(email: str, reset_link: str) -> None:
    """Send a password reset link via Resend."""
    if not resend.api_key:
        logger.warning("Resend API key is not configured; password reset email was not sent")
        return

    escaped_link = html.escape(reset_link)

    try:
        response = await resend.Emails.send_async({
            "from": f"LinkeFlow <{settings.FROM_EMAIL}>",
            "to": [email],
            "subject": "Reset your Link Easy password",
            "html": f"""
                <div style="font-family: Arial, sans-serif; max-width: 480px; margin: auto;">
                    <h2>Password Reset Request</h2>
                    <p>We received a request to reset your Linkeflow password.</p>
                    <p>Click the button below to reset your password:</p>
                    <div style="text-align: center; margin: 24px 0;">
                        <a href="{escaped_link}" style="display: inline-block; background-color: #0077b5; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold;">Reset Password</a>
                    </div>
                    <p style="color: #888; font-size: 13px;">This link expires in 30 minutes. If you did not request this, please ignore this email.</p>
                </div>
            """
        })
        logger.info(f"Password reset email sent successfully. ID: {response.id}")
    except Exception as e:
        logger.error(f"Failed to send password reset email via Resend: {str(e)}")
        raise EmailDeliveryError(f"Could not send password reset email: {str(e)}") from e
