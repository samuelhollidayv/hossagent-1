"""
Outbound email infrastructure for HossAgent.
Supports three modes: DRY_RUN, SENDGRID, SMTP

Environment Variables:
  EMAIL_MODE = DRY_RUN | SENDGRID | SMTP (defaults to DRY_RUN)
  
  For SENDGRID:
    SENDGRID_API_KEY
    SENDGRID_FROM_EMAIL
    SENDGRID_FROM_NAME (default: HossAgent)
    
  For SMTP:
    SMTP_HOST
    SMTP_PORT (default: 587)
    SMTP_USERNAME
    SMTP_PASSWORD
    SMTP_FROM_EMAIL
    SMTP_FROM_NAME (default: HossAgent)
    
  Throttling:
    MAX_EMAILS_PER_CYCLE (default: 10)
    MAX_EMAILS_PER_HOUR (default: 50)
    
  Deliverability:
    EMAIL_SEND_DELAY_MIN (default: 1) - minimum delay between sends in seconds
    EMAIL_SEND_DELAY_MAX (default: 5) - maximum delay between sends in seconds
"""
import os
import smtplib
import json
import time
import random
from enum import Enum
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path


class EmailMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    SENDGRID = "SENDGRID"
    SMTP = "SMTP"


@dataclass
class EmailResult:
    """Unified result object for all email send attempts."""
    success: bool
    mode: str  # "DRY_RUN", "SENDGRID", "SMTP"
    result: str  # "success", "failed", "dry_run", "throttled", "fallback"
    error: Optional[str] = None
    actually_sent: bool = False  # True only if email was really sent (not DRY_RUN)


@dataclass
class EmailAttempt:
    timestamp: str
    lead_name: str
    company: str
    to_email: str
    subject: str
    mode: str
    result: str  # "success", "failed", "dry_run", "throttled", "fallback"
    error: Optional[str] = None


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


def get_from_name() -> str:
    """Get the consistent From name for emails."""
    return os.getenv("SENDGRID_FROM_NAME", os.getenv("SMTP_FROM_NAME", "HossAgent"))


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
    try:
        return EmailMode(mode_str)
    except ValueError:
        print(f"[EMAIL] Warning: Invalid EMAIL_MODE '{mode_str}', falling back to DRY_RUN")
        return EmailMode.DRY_RUN


def validate_email_config() -> tuple[EmailMode, bool, str]:
    """
    Validate email configuration for the selected mode.
    
    Returns:
        (effective_mode, is_valid, message)
        If validation fails, effective_mode will be DRY_RUN.
    """
    mode = get_email_mode()
    
    if mode == EmailMode.DRY_RUN:
        return mode, True, "DRY_RUN mode - no credentials required"
    
    if mode == EmailMode.SENDGRID:
        api_key = os.getenv("SENDGRID_API_KEY")
        from_email = os.getenv("SENDGRID_FROM_EMAIL")
        
        if not api_key:
            msg = "SENDGRID_API_KEY not set - falling back to DRY_RUN"
            print(f"[EMAIL] Warning: {msg}")
            return EmailMode.DRY_RUN, False, msg
        if not from_email:
            msg = "SENDGRID_FROM_EMAIL not set - falling back to DRY_RUN"
            print(f"[EMAIL] Warning: {msg}")
            return EmailMode.DRY_RUN, False, msg
        
        return mode, True, "SendGrid configured"
    
    if mode == EmailMode.SMTP:
        host = os.getenv("SMTP_HOST")
        user = os.getenv("SMTP_USERNAME")
        password = os.getenv("SMTP_PASSWORD")
        from_email = os.getenv("SMTP_FROM_EMAIL")
        
        missing = []
        if not host:
            missing.append("SMTP_HOST")
        if not user:
            missing.append("SMTP_USERNAME")
        if not password:
            missing.append("SMTP_PASSWORD")
        if not from_email:
            missing.append("SMTP_FROM_EMAIL")
        
        if missing:
            msg = f"Missing SMTP config: {', '.join(missing)} - falling back to DRY_RUN"
            print(f"[EMAIL] Warning: {msg}")
            return EmailMode.DRY_RUN, False, msg
        
        return mode, True, "SMTP configured"
    
    return EmailMode.DRY_RUN, True, "Default DRY_RUN mode"


def get_max_emails_per_cycle() -> int:
    """Get the maximum number of emails to send per cycle."""
    try:
        return int(os.getenv("MAX_EMAILS_PER_CYCLE", "10"))
    except ValueError:
        return 10


def send_email_dry_run(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = "",
    is_fallback: bool = False
) -> EmailResult:
    """
    Simulate sending email without actually sending.
    Logs the attempt for visibility.
    """
    preview = body[:100].replace('\n', ' ')
    result_type = "fallback" if is_fallback else "dry_run"
    tag = "[DRY_RUN_FALLBACK]" if is_fallback else "[DRY_RUN]"
    print(f"[EMAIL]{tag} to={to_email} subject=\"{subject}\" preview=\"{preview}...\"")
    
    log_email_attempt(EmailAttempt(
        timestamp=datetime.utcnow().isoformat(),
        lead_name=lead_name,
        company=company,
        to_email=to_email,
        subject=subject,
        mode="DRY_RUN",
        result=result_type
    ))
    
    return EmailResult(
        success=False,
        mode="DRY_RUN",
        result=result_type,
        error=None,
        actually_sent=False
    )


def send_email_sendgrid(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = ""
) -> EmailResult:
    """Send email via SendGrid API."""
    try:
        import requests
        
        api_key = os.getenv("SENDGRID_API_KEY", "")
        from_email = os.getenv("SENDGRID_FROM_EMAIL", "")
        from_name = get_from_name()
        
        if not api_key or not from_email:
            raise ValueError("SendGrid credentials not configured")
        
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": from_email, "name": from_name},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}]
            },
            timeout=30
        )
        
        if response.status_code in [200, 201, 202]:
            print(f"[EMAIL][SUCCESS][SENDGRID] lead={lead_name} email={to_email} subject=\"{subject[:50]}...\"")
            log_email_attempt(EmailAttempt(
                timestamp=datetime.utcnow().isoformat(),
                lead_name=lead_name,
                company=company,
                to_email=to_email,
                subject=subject,
                mode="SENDGRID",
                result="success"
            ))
            return EmailResult(
                success=True,
                mode="SENDGRID",
                result="success",
                error=None,
                actually_sent=True
            )
        else:
            error_msg = f"Status {response.status_code}: {response.text[:200]}"
            print(f"[EMAIL][FAIL][SENDGRID] email={to_email} error=\"{error_msg}\"")
            log_email_attempt(EmailAttempt(
                timestamp=datetime.utcnow().isoformat(),
                lead_name=lead_name,
                company=company,
                to_email=to_email,
                subject=subject,
                mode="SENDGRID",
                result="failed",
                error=error_msg
            ))
            return EmailResult(
                success=False,
                mode="SENDGRID",
                result="failed",
                error=error_msg,
                actually_sent=False
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
            error=error_msg
        ))
        return EmailResult(
            success=False,
            mode="SENDGRID",
            result="failed",
            error=error_msg,
            actually_sent=False
        )


def send_email_smtp(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = ""
) -> EmailResult:
    """Send email via SMTP."""
    try:
        smtp_host = os.getenv("SMTP_HOST", "")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USERNAME", "")
        smtp_pass = os.getenv("SMTP_PASSWORD", "")
        from_email = os.getenv("SMTP_FROM_EMAIL") or smtp_user
        from_name = get_from_name()
        
        if not all([smtp_host, smtp_user, smtp_pass, from_email]):
            raise ValueError("SMTP credentials not configured")
        
        msg = MIMEMultipart()
        msg['From'] = f"{from_name} <{from_email}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        
        print(f"[EMAIL][SUCCESS][SMTP] lead={lead_name} email={to_email} subject=\"{subject[:50]}...\"")
        log_email_attempt(EmailAttempt(
            timestamp=datetime.utcnow().isoformat(),
            lead_name=lead_name,
            company=company,
            to_email=to_email,
            subject=subject,
            mode="SMTP",
            result="success"
        ))
        return EmailResult(
            success=True,
            mode="SMTP",
            result="success",
            error=None,
            actually_sent=True
        )
        
    except Exception as e:
        error_msg = str(e)
        print(f"[EMAIL][FAIL][SMTP] email={to_email} error=\"{error_msg}\"")
        log_email_attempt(EmailAttempt(
            timestamp=datetime.utcnow().isoformat(),
            lead_name=lead_name,
            company=company,
            to_email=to_email,
            subject=subject,
            mode="SMTP",
            result="failed",
            error=error_msg
        ))
        return EmailResult(
            success=False,
            mode="SMTP",
            result="failed",
            error=error_msg,
            actually_sent=False
        )


def send_email(
    to_email: str,
    subject: str,
    body: str,
    lead_name: str = "",
    company: str = ""
) -> EmailResult:
    """
    Unified email sending entrypoint.
    
    Determines the effective mode (with validation/fallback),
    then dispatches to the appropriate sender.
    
    Enforces both per-cycle and per-hour throttling limits.
    Applies deliverability delays between real sends.
    
    Args:
        to_email: Recipient email address
        subject: Email subject line
        body: Plain text email body
        lead_name: Name of the lead (for logging)
        company: Company name (for logging)
    
    Returns:
        EmailResult with success status, mode, and error details.
        This function NEVER crashes - all exceptions are caught.
    """
    try:
        effective_mode, is_valid, msg = validate_email_config()
        is_fallback = not is_valid and effective_mode == EmailMode.DRY_RUN
        
        if effective_mode == EmailMode.DRY_RUN:
            return send_email_dry_run(to_email, subject, body, lead_name, company, is_fallback=is_fallback)
        
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
        
        apply_send_delay()
        
        result: EmailResult
        if effective_mode == EmailMode.SENDGRID:
            result = send_email_sendgrid(to_email, subject, body, lead_name, company)
        elif effective_mode == EmailMode.SMTP:
            result = send_email_smtp(to_email, subject, body, lead_name, company)
        else:
            result = send_email_dry_run(to_email, subject, body, lead_name, company, is_fallback=is_fallback)
        
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
        - max_per_cycle: Throttle limit per cycle
        - max_per_hour: Throttle limit per hour
        - hourly: Current hourly counter status
    """
    configured_mode = os.getenv("EMAIL_MODE", "DRY_RUN").upper()
    effective_mode, is_valid, message = validate_email_config()
    hourly_status = get_hourly_counter_status()
    
    return {
        "mode": effective_mode.value,
        "configured_mode": configured_mode,
        "is_valid": is_valid,
        "message": message,
        "max_per_cycle": get_max_emails_per_cycle(),
        "max_per_hour": get_max_emails_per_hour(),
        "hourly": hourly_status
    }
