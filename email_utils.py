"""
Outbound email infrastructure for HossAgent.
Supports SendGrid API, SMTP fallback, and dry-run mode.

Environment Variables:
  SendGrid: SENDGRID_API_KEY, SENDGRID_FROM_EMAIL
  SMTP: SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL

If no credentials are configured, operates in dry-run mode (logs only).
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def get_email_mode() -> str:
    """
    Detect which email mode is available based on environment variables.
    Returns: 'sendgrid', 'smtp', or 'dry-run'
    """
    if os.getenv("SENDGRID_API_KEY"):
        return "sendgrid"
    if (os.getenv("SMTP_HOST")
        and os.getenv("SMTP_PORT")
        and os.getenv("SMTP_USERNAME")
        and os.getenv("SMTP_PASSWORD")):
        return "smtp"
    return "dry-run"


def send_email(recipient: str, subject: str, body: str) -> bool:
    """
    Send an email using either SendGrid, SMTP, or dry-run.
    
    Args:
        recipient: Email address to send to
        subject: Email subject line
        body: Plain text email body
    
    Returns:
        True if a real email was sent successfully, False otherwise.
        This function NEVER crashes the app - all exceptions are caught and logged.
    """
    mode = get_email_mode()
    
    try:
        if mode == "sendgrid":
            return _send_via_sendgrid(recipient, subject, body)
        elif mode == "smtp":
            return _send_via_smtp(recipient, subject, body)
        else:
            print(f"[EMAIL] DRY RUN -> To: {recipient}, Subject: {subject}")
            print(f"[EMAIL] Body preview: {body[:100]}...")
            return False
    except Exception as e:
        print(f"[EMAIL] Error sending email to {recipient}: {e}")
        return False


def _send_via_sendgrid(recipient: str, subject: str, body: str) -> bool:
    """Send email via SendGrid API."""
    try:
        import requests
        
        api_key = os.getenv("SENDGRID_API_KEY")
        from_email = os.getenv("SENDGRID_FROM_EMAIL", "noreply@hossagent.com")
        
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": recipient}]}],
                "from": {"email": from_email},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}]
            },
            timeout=30
        )
        
        if response.status_code in [200, 201, 202]:
            print(f"[EMAIL] Sent via SendGrid to {recipient}: {subject}")
            return True
        else:
            print(f"[EMAIL] SendGrid error: {response.status_code} - {response.text}")
            return False
            
    except ImportError:
        print("[EMAIL] SendGrid requires 'requests' library")
        return False
    except Exception as e:
        print(f"[EMAIL] SendGrid exception: {e}")
        return False


def _send_via_smtp(recipient: str, subject: str, body: str) -> bool:
    """Send email via SMTP."""
    try:
        smtp_host = os.getenv("SMTP_HOST", "")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME", "")
        smtp_pass = os.getenv("SMTP_PASSWORD", "")
        from_email = os.getenv("SMTP_FROM_EMAIL") or smtp_user
        
        if not all([smtp_host, smtp_user, smtp_pass]):
            print("[EMAIL] SMTP credentials incomplete")
            return False
        
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        
        print(f"[EMAIL] Sent via SMTP to {recipient}: {subject}")
        return True
        
    except Exception as e:
        print(f"[EMAIL] SMTP exception: {e}")
        return False
