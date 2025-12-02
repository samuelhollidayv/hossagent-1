"""
Release Mode Configuration for HossAgent.

Provides environment-driven safety rails for production deployment.

Environment Variables:
    RELEASE_MODE = PRODUCTION | STAGING | DEVELOPMENT (default: DEVELOPMENT)
    
PRODUCTION mode:
    - Startup banner: [RELEASE_MODE][PRODUCTION]
    - Strict validation of all credentials
    - Warnings for high-volume email settings
    - Enforces DRY_RUN fallback for missing credentials
    - All safety limits at production values

STAGING mode:
    - Startup banner: [RELEASE_MODE][STAGING]
    - Semi-strict validation (warnings but continues)
    - Lower throttle limits for testing
    - Good for pre-production testing

DEVELOPMENT mode (default):
    - Startup banner: [RELEASE_MODE][DEVELOPMENT]
    - Lenient configuration (DRY_RUN acceptable)
    - No high-volume warnings
    - Allows DummySeed providers
"""
import os
from enum import Enum
from typing import Dict, Any, List
from datetime import datetime, timedelta


class ReleaseMode(Enum):
    """Release mode enum for HossAgent deployment modes."""
    PRODUCTION = "PRODUCTION"
    STAGING = "STAGING"
    DEVELOPMENT = "DEVELOPMENT"


def get_release_mode() -> ReleaseMode:
    """
    Get current release mode from environment.
    
    Checks RELEASE_MODE env var:
    - PRODUCTION or TRUE -> ReleaseMode.PRODUCTION
    - STAGING -> ReleaseMode.STAGING
    - Anything else -> ReleaseMode.DEVELOPMENT
    """
    mode_str = os.getenv("RELEASE_MODE", "DEVELOPMENT").upper().strip()
    
    if mode_str in ("PRODUCTION", "TRUE"):
        return ReleaseMode.PRODUCTION
    elif mode_str == "STAGING":
        return ReleaseMode.STAGING
    else:
        return ReleaseMode.DEVELOPMENT


def is_release_mode() -> bool:
    """
    Check if system is running in Production mode.
    
    Returns True only for PRODUCTION mode.
    For backward compatibility with existing code.
    """
    return get_release_mode() == ReleaseMode.PRODUCTION


def is_production() -> bool:
    """Explicit check for PRODUCTION mode."""
    return get_release_mode() == ReleaseMode.PRODUCTION


def is_staging() -> bool:
    """Explicit check for STAGING mode."""
    return get_release_mode() == ReleaseMode.STAGING


def is_development() -> bool:
    """Explicit check for DEVELOPMENT mode."""
    return get_release_mode() == ReleaseMode.DEVELOPMENT


def get_release_mode_status() -> Dict[str, Any]:
    """Get current release mode configuration status."""
    mode = get_release_mode()
    email_mode = os.getenv("EMAIL_MODE", "DRY_RUN").upper()
    enable_stripe = os.getenv("ENABLE_STRIPE", "FALSE").upper() == "TRUE"
    max_emails_hour = int(os.getenv("MAX_EMAILS_PER_HOUR", "50"))
    lead_api_configured = bool(os.getenv("LEAD_SEARCH_API_KEY"))
    
    warnings = []
    errors = []
    
    if mode == ReleaseMode.PRODUCTION:
        if email_mode == "DRY_RUN":
            warnings.append("PRODUCTION mode but EMAIL_MODE=DRY_RUN - no real emails will be sent")
        
        if max_emails_hour > 100:
            warnings.append(f"MAX_EMAILS_PER_HOUR={max_emails_hour} is high for production")
        
        if not enable_stripe:
            warnings.append("PRODUCTION mode but ENABLE_STRIPE=FALSE - no payment links")
        
        if not lead_api_configured:
            warnings.append("PRODUCTION mode but no LEAD_SEARCH_API_KEY - using DummySeed")
        
        if email_mode in ["SENDGRID", "SMTP"]:
            if email_mode == "SENDGRID" and not os.getenv("SENDGRID_API_KEY"):
                errors.append("SENDGRID mode configured but SENDGRID_API_KEY not set")
            elif email_mode == "SMTP" and not (os.getenv("SMTP_HOST") and os.getenv("SMTP_USERNAME")):
                errors.append("SMTP mode configured but credentials incomplete")
    
    elif mode == ReleaseMode.STAGING:
        if email_mode not in ["DRY_RUN", "SENDGRID", "SMTP"]:
            warnings.append(f"Unrecognized EMAIL_MODE: {email_mode}")
        
        if max_emails_hour > 50:
            warnings.append(f"MAX_EMAILS_PER_HOUR={max_emails_hour} - consider lower for staging")
    
    return {
        "release_mode": mode.value,
        "is_production": mode == ReleaseMode.PRODUCTION,
        "is_staging": mode == ReleaseMode.STAGING,
        "is_development": mode == ReleaseMode.DEVELOPMENT,
        "email_mode": email_mode,
        "stripe_enabled": enable_stripe,
        "max_emails_per_hour": max_emails_hour,
        "lead_api_configured": lead_api_configured,
        "warnings": warnings,
        "errors": errors
    }


def print_startup_banners() -> None:
    """Print startup status banners for all subsystems based on release mode."""
    mode = get_release_mode()
    
    if mode == ReleaseMode.PRODUCTION:
        print("=" * 60)
        print("[RELEASE_MODE][PRODUCTION] HossAgent running in PRODUCTION mode")
        print("=" * 60)
    elif mode == ReleaseMode.STAGING:
        print("-" * 60)
        print("[RELEASE_MODE][STAGING] HossAgent running in STAGING mode")
        print("-" * 60)
    else:
        print("[RELEASE_MODE][DEVELOPMENT] HossAgent running in DEVELOPMENT mode")
    
    email_mode = os.getenv("EMAIL_MODE", "DRY_RUN").upper()
    print(f"[EMAIL][STARTUP] Mode: {email_mode}")
    
    max_emails_hour = int(os.getenv("MAX_EMAILS_PER_HOUR", "50"))
    if mode == ReleaseMode.PRODUCTION and max_emails_hour > 100:
        print(f"[PRODUCTION][HIGH_VOLUME_WARNING] MAX_EMAILS_PER_HOUR={max_emails_hour} - consider lower values for warm-up")
    
    if email_mode in ["SENDGRID", "SMTP"]:
        if email_mode == "SENDGRID":
            if not os.getenv("SENDGRID_API_KEY"):
                print("[DRY_RUN_FALLBACK] SENDGRID_API_KEY not set - will use DRY_RUN")
        elif email_mode == "SMTP":
            if not os.getenv("SMTP_HOST") or not os.getenv("SMTP_USERNAME"):
                print("[DRY_RUN_FALLBACK] SMTP credentials incomplete - will use DRY_RUN")
    
    lead_api = os.getenv("LEAD_SEARCH_API_KEY")
    if mode == ReleaseMode.PRODUCTION and not lead_api:
        print("[PRODUCTION][WARNING] No LEAD_SEARCH_API_KEY - using DummySeed provider")
    elif lead_api:
        print("[LEADS][STARTUP] SearchApi configured")
    else:
        print("[LEADS][STARTUP] Using DummySeed provider (dev mode)")


def get_throttle_defaults() -> Dict[str, int]:
    """
    Get default throttle values based on release mode.
    
    Returns sensible defaults that can be overridden by env vars.
    """
    mode = get_release_mode()
    
    if mode == ReleaseMode.PRODUCTION:
        return {
            "max_emails_per_cycle": 10,
            "max_emails_per_hour": 50,
            "max_new_leads_per_cycle": 10
        }
    elif mode == ReleaseMode.STAGING:
        return {
            "max_emails_per_cycle": 5,
            "max_emails_per_hour": 20,
            "max_new_leads_per_cycle": 5
        }
    else:
        return {
            "max_emails_per_cycle": 10,
            "max_emails_per_hour": 50,
            "max_new_leads_per_cycle": 10
        }


def generate_daily_summary(
    leads_data: Dict[str, Any],
    email_data: Dict[str, Any],
    invoice_data: Dict[str, Any],
    payment_data: Dict[str, Any],
    hours: int = 24
) -> Dict[str, Any]:
    """
    Generate a summary of system activity for the last N hours.
    
    Args:
        leads_data: Lead statistics from lead_service
        email_data: Email statistics from email_utils
        invoice_data: Invoice statistics from database
        payment_data: Payment statistics from stripe_utils
        hours: Hours to look back (default 24)
    
    Returns:
        Dict with summary statistics
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    mode = get_release_mode()
    
    return {
        "period": {
            "hours": hours,
            "start": cutoff.isoformat(),
            "end": datetime.utcnow().isoformat()
        },
        "leads": leads_data,
        "emails": email_data,
        "invoices": invoice_data,
        "payments": payment_data,
        "generated_at": datetime.utcnow().isoformat(),
        "release_mode": mode.value
    }
