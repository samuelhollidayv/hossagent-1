"""
Outbound email infrastructure for HossAgent.
Supports three modes: DRY_RUN, SENDGRID, SES

Domain: hossagent.net (authenticated)

Environment Variables:
  EMAIL_MODE = DRY_RUN | SENDGRID | SES (defaults to DRY_RUN)
  
  For SENDGRID (required when EMAIL_MODE=SENDGRID):
    SENDGRID_API_KEY       - SendGrid API key
    OUTBOUND_FROM          - Sending email (e.g., hello@hossagent.net)
    OUTBOUND_REPLY_TO      - Reply-to email address
    OUTBOUND_DISPLAY_NAME  - Display name (e.g., HossAgent)
    
  For SES (required when EMAIL_MODE=SES):
    AWS_SES_ACCESS_KEY     - AWS access key ID
    AWS_SES_SECRET_KEY     - AWS secret access key
    AWS_SES_REGION         - AWS region (e.g., us-east-1)
    OUTBOUND_FROM          - Verified sending email (e.g., hello@hossagent.net)
    OUTBOUND_DISPLAY_NAME  - Display name (e.g., HossAgent)
    
  Throttling:
    MAX_EMAILS_PER_CYCLE (default: 10)
    MAX_EMAILS_PER_HOUR (default: 50)
    
  Deliverability:
    EMAIL_SEND_DELAY_MIN (default: 1) - minimum delay between sends in seconds
    EMAIL_SEND_DELAY_MAX (default: 5) - maximum delay between sends in seconds
"""
import os
import json
import time
import random
import html
from enum import Enum
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path


class EmailMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    SENDGRID = "SENDGRID"
    SES = "SES"


@dataclass
class EmailResult:
    """Unified result object for all email send attempts."""
    success: bool
    mode: str  # "DRY_RUN", "SENDGRID"
    result: str  # "success", "failed", "dry_run", "throttled"
    error: Optional[str] = None
    actually_sent: bool = False  # True only if email was really sent (not DRY_RUN)
    sendgrid_response: Optional[Dict[str, Any]] = None  # Full SendGrid response for debugging


@dataclass
class EmailAttempt:
    timestamp: str
    lead_name: str
    company: str
    to_email: str
    subject: str
    mode: str
    result: str  # "success", "failed", "dry_run", "throttled"
    error: Optional[str] = None
    sending_domain: Optional[str] = None
    sendgrid_headers: Optional[Dict[str, Any]] = None


EMAIL_LOG_FILE = Path("email_attempts.json")
HOURLY_COUNTER_FILE = Path("email_hourly_counter.json")
MAX_LOG_ENTRIES = 5000  # Capped log entries to prevent unbounded growth


def get_send_delay_range() -> Tuple[int, int]:
    """Get the delay range between email sends for deliverability."""
    try:
        min_delay = int(os.getenv("EMAIL_SEND_DELAY_MIN", "1"))
        max_delay = int(os.getenv("EMAIL_SEND_DELAY_MAX", "5"))
        return max(0, min_delay), max(min_delay, max_delay)
    except ValueError:
        return 1, 5


def apply_send_delay() -> None:
    """Apply a random delay between email sends for deliverability."""
    min_delay, max_delay = get_send_delay_range()
    if min_delay > 0 or max_delay > 0:
        delay = random.uniform(min_delay, max_delay)
        time.sleep(delay)


def extract_domain(email: str) -> str:
    """Extract domain from email address."""
    if "@" in email:
        return email.split("@")[1]
    return ""


def _load_email_log() -> List[Dict[str, Any]]:
    """Load email attempt log from file."""
    try:
        if EMAIL_LOG_FILE.exists():
            with open(EMAIL_LOG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_email_log(entries: List[Dict[str, Any]]) -> None:
    """Save email attempt log to file."""
    try:
        entries = entries[-MAX_LOG_ENTRIES:]
        with open(EMAIL_LOG_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        print(f"[EMAIL] Warning: Could not save email log: {e}")


def log_email_attempt(attempt: EmailAttempt) -> None:
    """Log an email attempt for admin console visibility."""
    entries = _load_email_log()
    entries.append(asdict(attempt))
    _save_email_log(entries)


def get_email_log(limit: int = 10) -> List[Dict[str, Any]]:
    """Get the last N email attempts for display in admin console."""
    entries = _load_email_log()
    return entries[-limit:]


def _load_hourly_counter() -> Dict[str, Any]:
    """Load hourly email counter from file."""
    try:
        if HOURLY_COUNTER_FILE.exists():
            with open(HOURLY_COUNTER_FILE, "r") as f:
                data = json.load(f)
                hour_key = datetime.utcnow().strftime("%Y-%m-%d-%H")
                if data.get("hour") == hour_key:
                    return data
    except Exception:
        pass
    return {"hour": datetime.utcnow().strftime("%Y-%m-%d-%H"), "count": 0}


def _save_hourly_counter(data: Dict[str, Any]) -> None:
    """Save hourly email counter to file."""
    try:
        with open(HOURLY_COUNTER_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[EMAIL] Warning: Could not save hourly counter: {e}")


def get_max_emails_per_hour() -> int:
    """Get the maximum number of emails to send per hour."""
    try:
        return int(os.getenv("MAX_EMAILS_PER_HOUR", "50"))
    except ValueError:
        return 50


def check_hourly_limit() -> Tuple[bool, int, int]:
    """
    Check if hourly email limit has been reached.
    
    Returns:
        (can_send, current_count, max_count)
    """
    counter = _load_hourly_counter()
    max_per_hour = get_max_emails_per_hour()
    current = counter.get("count", 0)
    return current < max_per_hour, current, max_per_hour


def increment_hourly_counter() -> None:
    """Increment the hourly email counter after a successful send."""
    counter = _load_hourly_counter()
    counter["count"] = counter.get("count", 0) + 1
    _save_hourly_counter(counter)


def get_hourly_counter_status() -> Dict[str, Any]:
    """Get current hourly counter status for admin display."""
    counter = _load_hourly_counter()
    max_per_hour = get_max_emails_per_hour()
    return {
        "hour": counter.get("hour"),
        "count": counter.get("count", 0),
        "max": max_per_hour,
        "remaining": max_per_hour - counter.get("count", 0)
    }


def get_email_mode() -> EmailMode:
    """
    Get the configured email mode from environment.
    Falls back to DRY_RUN if EMAIL_MODE is not set or invalid.
    """
    mode_str = os.getenv("EMAIL_MODE", "DRY_RUN").upper()
    
    # Map legacy SMTP mode to DRY_RUN (SMTP no longer supported)
    if mode_str == "SMTP":
        print("[EMAIL][WARNING] SMTP mode is deprecated. Use SENDGRID mode with hossagent.net domain.")
        return EmailMode.DRY_RUN
    
    try:
        return EmailMode(mode_str)
    except ValueError:
        print(f"[EMAIL] Warning: Invalid EMAIL_MODE '{mode_str}', falling back to DRY_RUN")
        return EmailMode.DRY_RUN


def get_sendgrid_config() -> Dict[str, str]:
    """
    Get SendGrid configuration from environment variables.
    
    Returns dict with:
        - api_key: SENDGRID_API_KEY
        - from_email: OUTBOUND_FROM
        - reply_to: OUTBOUND_REPLY_TO
        - display_name: OUTBOUND_DISPLAY_NAME
    """
    return {
        "api_key": os.getenv("SENDGRID_API_KEY", ""),
        "from_email": os.getenv("OUTBOUND_FROM", ""),
        "reply_to": os.getenv("OUTBOUND_REPLY_TO", ""),
        "display_name": os.getenv("OUTBOUND_DISPLAY_NAME", "HossAgent"),
    }


def validate_sendgrid_config() -> Tuple[bool, List[str]]:
    """
    Validate that all required SendGrid environment variables are set.
    
    Returns:
        (is_valid, missing_vars)
    """
    config = get_sendgrid_config()
    required_vars = {
        "SENDGRID_API_KEY": config["api_key"],
        "OUTBOUND_FROM": config["from_email"],
        "OUTBOUND_REPLY_TO": config["reply_to"],
        "OUTBOUND_DISPLAY_NAME": config["display_name"],
    }
    
    missing = [var for var, value in required_vars.items() if not value]
    return len(missing) == 0, missing


def validate_ses_config() -> Tuple[bool, List[str]]:
    """
    Validate that all required SES environment variables are set.
    
    Returns:
        (is_valid, missing_vars)
    """
    config = get_ses_config()
    required_vars = {
        "AWS_SES_ACCESS_KEY": config["access_key"],
        "AWS_SES_SECRET_KEY": config["secret_key"],
        "OUTBOUND_FROM": config["from_email"],
    }
    
    missing = [var for var, value in required_vars.items() if not value]
    return len(missing) == 0, missing


def validate_email_config() -> tuple[EmailMode, bool, str]:
    """
    Validate email configuration for the selected mode.
    
    Returns:
        (effective_mode, is_valid, message)
        If validation fails, returns DRY_RUN with error message (no silent fallback).
    """
    mode = get_email_mode()
    
    if mode == EmailMode.DRY_RUN:
        return mode, True, "DRY_RUN mode - no credentials required"
    
    if mode == EmailMode.SENDGRID:
        is_valid, missing = validate_sendgrid_config()
        
        if not is_valid:
            error_msg = f"SENDGRID mode requires these environment variables: {', '.join(missing)}"
            print(f"[EMAIL][ERROR] {error_msg}")
            return EmailMode.DRY_RUN, False, error_msg
        
        config = get_sendgrid_config()
        domain = extract_domain(config["from_email"])
        return mode, True, f"SendGrid configured with domain: {domain}"
    
    if mode == EmailMode.SES:
        is_valid, missing = validate_ses_config()
        
        if not is_valid:
            error_msg = f"SES mode requires these environment variables: {', '.join(missing)}"
            print(f"[EMAIL][ERROR] {error_msg}")
            return EmailMode.DRY_RUN, False, error_msg
        
        config = get_ses_config()
        domain = extract_domain(config["from_email"])
        return mode, True, f"Amazon SES configured with domain: {domain} (region: {config['region']})"
    
    return EmailMode.DRY_RUN, True, "Default DRY_RUN mode"


def get_max_emails_per_cycle() -> int:
    """Get the maximum number of emails to send per cycle."""
    try:
        return int(os.getenv("MAX_EMAILS_PER_CYCLE", "10"))
    except ValueError:
        return 10


def plain_to_html(plain_text: str) -> str:
    """
    Convert plain text email body to simple HTML.
    Preserves paragraphs and line breaks.
    """
    escaped = html.escape(plain_text)
    
    paragraphs = escaped.split('\n\n')
    html_paragraphs = []
    
    for para in paragraphs:
        lines = para.split('\n')
        html_para = '<br>\n'.join(lines)
        html_paragraphs.append(f'<p style="margin: 0 0 16px 0; line-height: 1.5;">{html_para}</p>')
    
    html_body = '\n'.join(html_paragraphs)
    
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; font-size: 14px; color: #333333; max-width: 600px; margin: 0 auto; padding: 20px;">
{html_body}
</body>
</html>"""


def send_email_dry_run(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = "",
    cc_email: Optional[str] = None,
    reply_to: Optional[str] = None
) -> EmailResult:
    """
    Simulate sending email without actually sending.
    Logs the full email content for review.
    """
    config = get_sendgrid_config()
    from_email = config["from_email"] or "hello@hossagent.net"
    display_name = config["display_name"] or "HossAgent"
    actual_reply_to = reply_to or config["reply_to"] or from_email
    sending_domain = extract_domain(from_email)
    
    print(f"\n{'='*60}")
    print(f"[EMAIL][DRY_RUN] Simulated Send")
    print(f"{'='*60}")
    print(f"  From: {display_name} <{from_email}>")
    print(f"  To: {to_email}")
    if cc_email:
        print(f"  CC: {cc_email}")
    print(f"  Reply-To: {actual_reply_to}")
    print(f"  Subject: {subject}")
    print(f"  Domain: {sending_domain}")
    print(f"  Lead: {lead_name} ({company})")
    print(f"  Body Preview: {body[:200]}...")
    print(f"{'='*60}\n")
    
    log_email_attempt(EmailAttempt(
        timestamp=datetime.utcnow().isoformat(),
        lead_name=lead_name,
        company=company,
        to_email=to_email,
        subject=subject,
        mode="DRY_RUN",
        result="dry_run",
        sending_domain=sending_domain
    ))
    
    return EmailResult(
        success=True,  # DRY_RUN is considered "successful" for testing
        mode="DRY_RUN",
        result="dry_run",
        error=None,
        actually_sent=False
    )


def send_email_sendgrid(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = "",
    cc_email: Optional[str] = None,
    reply_to_override: Optional[str] = None
) -> EmailResult:
    """
    Send email via SendGrid API using authenticated hossagent.net domain.
    
    Sends multipart message with both plain text and HTML versions.
    Logs detailed response including domain authentication status.
    
    Email headers:
        From: OUTBOUND_DISPLAY_NAME <OUTBOUND_FROM>
        To: lead_email (the prospect)
        CC: customer.email (for visibility)
        Reply-To: OUTBOUND_REPLY_TO (or override)
    """
    try:
        import requests
        
        config = get_sendgrid_config()
        api_key = config["api_key"]
        from_email = config["from_email"]
        display_name = config["display_name"]
        default_reply_to = config["reply_to"]
        
        # Validate required config
        if not all([api_key, from_email]):
            raise ValueError("SendGrid configuration incomplete. Required: SENDGRID_API_KEY, OUTBOUND_FROM")
        
        sending_domain = extract_domain(from_email)
        actual_reply_to = reply_to_override or default_reply_to or from_email
        
        # Build HTML version
        html_body = plain_to_html(body)
        
        # Build personalization (To + optional CC)
        personalization = {"to": [{"email": to_email}]}
        if cc_email:
            personalization["cc"] = [{"email": cc_email}]
        
        # Build mail payload with multipart content
        mail_data = {
            "personalizations": [personalization],
            "from": {
                "email": from_email,
                "name": display_name
            },
            "reply_to": {
                "email": actual_reply_to
            },
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": body},
                {"type": "text/html", "value": html_body}
            ]
        }
        
        # Log outbound details before sending
        print(f"\n[EMAIL][SENDGRID] Preparing send...")
        print(f"  From: {display_name} <{from_email}>")
        print(f"  To: {to_email}")
        if cc_email:
            print(f"  CC: {cc_email}")
        print(f"  Reply-To: {actual_reply_to}")
        print(f"  Subject: {subject[:60]}...")
        print(f"  Domain: {sending_domain}")
        
        # Send via SendGrid API
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=mail_data,
            timeout=30
        )
        
        # Parse response
        response_headers = dict(response.headers)
        response_body = response.text if response.text else "{}"
        
        # Extract useful SendGrid headers for debugging
        sendgrid_debug = {
            "status_code": response.status_code,
            "x_message_id": response_headers.get("X-Message-Id", ""),
            "content_length": response_headers.get("Content-Length", ""),
            "date": response_headers.get("Date", ""),
            "response_body": response_body[:500] if response_body else ""
        }
        
        if response.status_code in [200, 201, 202]:
            print(f"[EMAIL][SUCCESS][SENDGRID] Sent to {to_email}")
            print(f"  Message-ID: {sendgrid_debug['x_message_id']}")
            print(f"  Domain: {sending_domain} (authenticated)")
            
            log_email_attempt(EmailAttempt(
                timestamp=datetime.utcnow().isoformat(),
                lead_name=lead_name,
                company=company,
                to_email=to_email,
                subject=subject,
                mode="SENDGRID",
                result="success",
                sending_domain=sending_domain,
                sendgrid_headers=sendgrid_debug
            ))
            
            return EmailResult(
                success=True,
                mode="SENDGRID",
                result="success",
                error=None,
                actually_sent=True,
                sendgrid_response=sendgrid_debug
            )
        else:
            error_msg = f"SendGrid API error {response.status_code}: {response_body[:300]}"
            print(f"[EMAIL][FAIL][SENDGRID] {error_msg}")
            print(f"  Full response: {response_body}")
            
            log_email_attempt(EmailAttempt(
                timestamp=datetime.utcnow().isoformat(),
                lead_name=lead_name,
                company=company,
                to_email=to_email,
                subject=subject,
                mode="SENDGRID",
                result="failed",
                error=error_msg,
                sending_domain=sending_domain,
                sendgrid_headers=sendgrid_debug
            ))
            
            return EmailResult(
                success=False,
                mode="SENDGRID",
                result="failed",
                error=error_msg,
                actually_sent=False,
                sendgrid_response=sendgrid_debug
            )
            
    except ImportError:
        error_msg = "'requests' library not available"
        print(f"[EMAIL][FAIL][SENDGRID] {error_msg}")
        return EmailResult(
            success=False,
            mode="SENDGRID",
            result="failed",
            error=error_msg,
            actually_sent=False
        )
    except Exception as e:
        error_msg = str(e)
        print(f"[EMAIL][FAIL][SENDGRID] Exception: {error_msg}")
        
        log_email_attempt(EmailAttempt(
            timestamp=datetime.utcnow().isoformat(),
            lead_name=lead_name,
            company=company,
            to_email=to_email,
            subject=subject,
            mode="SENDGRID",
            result="failed",
            error=error_msg,
            sending_domain=extract_domain(get_sendgrid_config().get("from_email", ""))
        ))
        
        return EmailResult(
            success=False,
            mode="SENDGRID",
            result="failed",
            error=error_msg,
            actually_sent=False
        )


def get_ses_config() -> Dict[str, str]:
    """Get Amazon SES configuration from environment variables."""
    return {
        "access_key": os.getenv("AWS_SES_ACCESS_KEY", ""),
        "secret_key": os.getenv("AWS_SES_SECRET_KEY", ""),
        "region": os.getenv("AWS_SES_REGION", "us-east-1"),
        "from_email": os.getenv("OUTBOUND_FROM", "hello@hossagent.net"),
        "display_name": os.getenv("OUTBOUND_DISPLAY_NAME", "HossAgent"),
    }


def send_email_ses(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = "",
    cc_email: Optional[str] = None,
    reply_to_override: Optional[str] = None
) -> EmailResult:
    """
    Send email via Amazon SES API.
    
    Uses boto3 SES client with MIME multipart for HTML + plain text.
    
    Email headers:
        From: OUTBOUND_DISPLAY_NAME <OUTBOUND_FROM>
        To: lead_email (the prospect)
        CC: customer.email (for visibility)
        Reply-To: customer.email (or override)
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        
        config = get_ses_config()
        access_key = config["access_key"]
        secret_key = config["secret_key"]
        region = config["region"]
        from_email = config["from_email"]
        display_name = config["display_name"]
        
        if not all([access_key, secret_key, from_email]):
            raise ValueError("SES configuration incomplete. Required: AWS_SES_ACCESS_KEY, AWS_SES_SECRET_KEY, OUTBOUND_FROM")
        
        sending_domain = extract_domain(from_email)
        actual_reply_to = reply_to_override or from_email
        
        ses_client = boto3.client(
            'ses',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{display_name} <{from_email}>"
        msg['To'] = to_email
        if cc_email:
            msg['Cc'] = cc_email
        msg['Reply-To'] = actual_reply_to
        
        html_body = plain_to_html(body)
        
        part_text = MIMEText(body, 'plain')
        part_html = MIMEText(html_body, 'html')
        msg.attach(part_text)
        msg.attach(part_html)
        
        destinations = [to_email]
        if cc_email:
            destinations.append(cc_email)
        
        print(f"\n[EMAIL][SES] Preparing send...")
        print(f"  From: {display_name} <{from_email}>")
        print(f"  To: {to_email}")
        if cc_email:
            print(f"  CC: {cc_email}")
        print(f"  Reply-To: {actual_reply_to}")
        print(f"  Subject: {subject[:60]}...")
        print(f"  Domain: {sending_domain}")
        
        response = ses_client.send_raw_email(
            Source=f"{display_name} <{from_email}>",
            Destinations=destinations,
            RawMessage={'Data': msg.as_string()}
        )
        
        message_id = response.get('MessageId', '')
        
        print(f"[EMAIL][SUCCESS][SES] Sent to {to_email}")
        print(f"  Message-ID: {message_id}")
        print(f"  Domain: {sending_domain}")
        
        log_email_attempt(EmailAttempt(
            timestamp=datetime.utcnow().isoformat(),
            lead_name=lead_name,
            company=company,
            to_email=to_email,
            subject=subject,
            mode="SES",
            result="success",
            sending_domain=sending_domain
        ))
        
        return EmailResult(
            success=True,
            mode="SES",
            result="success",
            error=None,
            actually_sent=True,
            sendgrid_response={"message_id": message_id}
        )
        
    except ImportError:
        error_msg = "'boto3' library not available"
        print(f"[EMAIL][FAIL][SES] {error_msg}")
        return EmailResult(
            success=False,
            mode="SES",
            result="failed",
            error=error_msg,
            actually_sent=False
        )
    except Exception as e:
        error_msg = str(e)
        print(f"[EMAIL][FAIL][SES] Exception: {error_msg}")
        
        log_email_attempt(EmailAttempt(
            timestamp=datetime.utcnow().isoformat(),
            lead_name=lead_name,
            company=company,
            to_email=to_email,
            subject=subject,
            mode="SES",
            result="failed",
            error=error_msg,
            sending_domain=extract_domain(get_ses_config().get("from_email", ""))
        ))
        
        return EmailResult(
            success=False,
            mode="SES",
            result="failed",
            error=error_msg,
            actually_sent=False
        )


def send_email(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = "",
    cc_email: Optional[str] = None,
    reply_to: Optional[str] = None
) -> EmailResult:
    """
    Unified email sending entrypoint.
    
    Determines the effective mode (with validation),
    then dispatches to the appropriate sender.
    
    In SENDGRID mode:
        - Uses authenticated hossagent.net domain
        - Sends multipart (plain + HTML)
        - NO fallback to other providers
        
    In DRY_RUN mode:
        - Simulates send and logs for review
        - Does NOT call SendGrid API
    
    Enforces both per-cycle and per-hour throttling limits.
    Applies deliverability delays between real sends.
    
    Args:
        to_email: Recipient email address (the lead/prospect)
        subject: Email subject line
        body: Plain text email body
        lead_name: Name of the lead (for logging)
        company: Company name (for logging)
        cc_email: Optional CC email address (the customer)
        reply_to: Optional Reply-To override
    
    Returns:
        EmailResult with success status, mode, and error details.
        This function NEVER crashes - all exceptions are caught.
    """
    try:
        effective_mode, is_valid, msg = validate_email_config()
        
        # In DRY_RUN mode, simulate without sending
        if effective_mode == EmailMode.DRY_RUN:
            if not is_valid:
                print(f"[EMAIL][CONFIG_ERROR] {msg}")
            return send_email_dry_run(to_email, subject, body, lead_name, company, cc_email=cc_email, reply_to=reply_to)
        
        # Check hourly rate limit
        can_send, current, max_hour = check_hourly_limit()
        if not can_send:
            error_msg = f"Hourly limit reached: {current}/{max_hour}"
            print(f"[EMAIL][THROTTLED] hour_count={current}/{max_hour} email={to_email}")
            log_email_attempt(EmailAttempt(
                timestamp=datetime.utcnow().isoformat(),
                lead_name=lead_name,
                company=company,
                to_email=to_email,
                subject=subject,
                mode=effective_mode.value,
                result="throttled",
                error=error_msg
            ))
            return EmailResult(
                success=False,
                mode=effective_mode.value,
                result="throttled",
                error=error_msg,
                actually_sent=False
            )
        
        # Apply deliverability delay
        apply_send_delay()
        
        # Route to appropriate email provider
        if effective_mode == EmailMode.SES:
            result = send_email_ses(
                to_email, subject, body, lead_name, company,
                cc_email=cc_email, reply_to_override=reply_to
            )
        else:
            result = send_email_sendgrid(
                to_email, subject, body, lead_name, company,
                cc_email=cc_email, reply_to_override=reply_to
            )
        
        # Increment hourly counter on successful send
        if result.actually_sent:
            increment_hourly_counter()
        
        return result
            
    except Exception as e:
        error_msg = str(e)
        print(f"[EMAIL][FAIL] Unexpected error: {error_msg}")
        return EmailResult(
            success=False,
            mode="UNKNOWN",
            result="failed",
            error=error_msg,
            actually_sent=False
        )


def send_email_legacy(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = ""
) -> bool:
    """
    Legacy wrapper that returns bool for backward compatibility.
    Use send_email() for the full EmailResult object.
    """
    result = send_email(to_email, subject, body, lead_name, company)
    return result.actually_sent


def get_email_status() -> Dict[str, Any]:
    """
    Get current email configuration status for admin display.
    
    Returns dict with:
        - mode: Current effective mode
        - configured_mode: What EMAIL_MODE env var says
        - is_valid: Whether config is valid
        - message: Status message
        - domain: Sending domain (if configured)
        - max_per_cycle: Throttle limit per cycle
        - max_per_hour: Throttle limit per hour
        - hourly: Current hourly counter status
    """
    configured_mode = os.getenv("EMAIL_MODE", "DRY_RUN").upper()
    effective_mode, is_valid, message = validate_email_config()
    hourly_status = get_hourly_counter_status()
    
    config = get_sendgrid_config()
    sending_domain = extract_domain(config["from_email"]) if config["from_email"] else ""
    
    return {
        "mode": effective_mode.value,
        "configured_mode": configured_mode,
        "is_valid": is_valid,
        "message": message,
        "domain": sending_domain,
        "from_email": config["from_email"],
        "display_name": config["display_name"],
        "reply_to": config["reply_to"],
        "max_per_cycle": get_max_emails_per_cycle(),
        "max_per_hour": get_max_emails_per_hour(),
        "hourly": hourly_status
    }
