"""
Release Mode Configuration for HossAgent.

Provides environment-driven safety rails for production deployment.

Environment Variables:
    RELEASE_MODE = PRODUCTION | SANDBOX (default: SANDBOX)
    
    Aliases:
    - SANDBOX, DEVELOPMENT, FALSE -> SANDBOX mode
    - PRODUCTION, TRUE -> PRODUCTION mode
    
PRODUCTION mode:
    - Startup banner: [RELEASE_MODE][PRODUCTION]
    - Uses Apollo.io for lead generation (ONLY source - no fallbacks)
    - Sends real emails (if EMAIL_MODE=SMTP or SENDGRID)
    - Strict validation of all credentials
    - Lead generation pauses if Apollo not connected

SANDBOX mode (default):
    - Startup banner: [RELEASE_MODE][SANDBOX]
    - Lead generation paused (requires Apollo connection)
    - Safe for testing - must explicitly opt into production
    - Lenient configuration (DRY_RUN acceptable)

To change modes, update env vars in Replit Secrets:
    RELEASE_MODE=PRODUCTION  # Enable real lead sources + full pipeline
    EMAIL_MODE=SMTP          # Enable real email sending
    APOLLO_API_KEY=xxx       # Required for lead generation
"""
import os
from enum import Enum
from typing import Dict, Any, List, Tuple
from datetime import datetime, timedelta


class ReleaseMode(Enum):
    """Release mode enum for HossAgent deployment modes."""
    PRODUCTION = "PRODUCTION"
    SANDBOX = "SANDBOX"
    # Legacy aliases - map to SANDBOX
    STAGING = "SANDBOX"
    DEVELOPMENT = "SANDBOX"


def get_release_mode() -> ReleaseMode:
    """
    Get current release mode from environment.
    
    Checks RELEASE_MODE env var:
    - PRODUCTION or TRUE -> ReleaseMode.PRODUCTION
    - SANDBOX, DEVELOPMENT, or anything else -> ReleaseMode.SANDBOX (safe default)
    
    SANDBOX is the default to ensure safe behavior - must explicitly opt into PRODUCTION.
    """
    mode_str = os.getenv("RELEASE_MODE", "SANDBOX").upper().strip()
    
    if mode_str in ("PRODUCTION", "TRUE"):
        return ReleaseMode.PRODUCTION
    else:
        # Default to SANDBOX for safety
        return ReleaseMode.SANDBOX


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


def is_sandbox() -> bool:
    """Explicit check for SANDBOX mode."""
    return get_release_mode() == ReleaseMode.SANDBOX


# Legacy aliases for backward compatibility
def is_staging() -> bool:
    """Legacy alias - maps to SANDBOX."""
    return is_sandbox()


def is_development() -> bool:
    """Legacy alias - maps to SANDBOX."""
    return is_sandbox()


def get_release_mode_status() -> Dict[str, Any]:
    """
    Get current release mode configuration status for admin display.
    
    Returns comprehensive status including mode, email config, and any warnings/errors.
    """
    mode = get_release_mode()
    email_mode = os.getenv("EMAIL_MODE", "DRY_RUN").upper()
    enable_stripe = os.getenv("ENABLE_STRIPE", "FALSE").upper() == "TRUE"
    max_emails_hour = int(os.getenv("MAX_EMAILS_PER_HOUR", "50"))
    lead_api_configured = bool(os.getenv("LEAD_SEARCH_API_KEY"))
    
    # SMTP credential check
    smtp_configured = bool(
        os.getenv("SMTP_HOST") and 
        os.getenv("SMTP_USERNAME") and 
        os.getenv("SMTP_PASSWORD") and
        os.getenv("SMTP_FROM_EMAIL")
    )
    
    warnings = []
    errors = []
    
    if mode == ReleaseMode.PRODUCTION:
        if email_mode == "DRY_RUN":
            warnings.append("PRODUCTION mode but EMAIL_MODE=DRY_RUN - no real emails will be sent")
        
        if max_emails_hour > 100:
            warnings.append(f"MAX_EMAILS_PER_HOUR={max_emails_hour} is high for production")
        
        if not enable_stripe:
            warnings.append("PRODUCTION mode but ENABLE_STRIPE=FALSE - no payment links")
        
        if not os.getenv("APOLLO_API_KEY"):
            warnings.append("PRODUCTION mode but no APOLLO_API_KEY - lead generation PAUSED")
        
        if email_mode == "SENDGRID" and not os.getenv("SENDGRID_API_KEY"):
            errors.append("SENDGRID mode configured but SENDGRID_API_KEY not set - will fallback to DRY_RUN")
        elif email_mode == "SMTP" and not smtp_configured:
            missing = []
            if not os.getenv("SMTP_HOST"): missing.append("SMTP_HOST")
            if not os.getenv("SMTP_USERNAME"): missing.append("SMTP_USERNAME")
            if not os.getenv("SMTP_PASSWORD"): missing.append("SMTP_PASSWORD")
            if not os.getenv("SMTP_FROM_EMAIL"): missing.append("SMTP_FROM_EMAIL")
            errors.append(f"SMTP mode configured but missing: {', '.join(missing)} - will fallback to DRY_RUN")
    
    elif mode == ReleaseMode.SANDBOX:
        if email_mode not in ["DRY_RUN", "SENDGRID", "SMTP"]:
            warnings.append(f"Unrecognized EMAIL_MODE: {email_mode}")
    
    # Determine effective email mode (accounting for fallbacks)
    effective_email_mode = email_mode
    if email_mode == "SMTP" and not smtp_configured:
        effective_email_mode = "DRY_RUN (fallback)"
    elif email_mode == "SENDGRID" and not os.getenv("SENDGRID_API_KEY"):
        effective_email_mode = "DRY_RUN (fallback)"
    
    return {
        "release_mode": mode.value,
        "is_production": mode == ReleaseMode.PRODUCTION,
        "is_sandbox": mode == ReleaseMode.SANDBOX,
        "email_mode": email_mode,
        "effective_email_mode": effective_email_mode,
        "smtp_configured": smtp_configured,
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
    else:
        print("[RELEASE_MODE][SANDBOX] HossAgent running in SANDBOX mode")
    
    email_mode = os.getenv("EMAIL_MODE", "DRY_RUN").upper()
    print(f"[EMAIL][STARTUP] Mode: {email_mode}")
    
    # Check SMTP credentials
    smtp_configured = bool(
        os.getenv("SMTP_HOST") and 
        os.getenv("SMTP_USERNAME") and 
        os.getenv("SMTP_PASSWORD") and
        os.getenv("SMTP_FROM_EMAIL")
    )
    
    max_emails_hour = int(os.getenv("MAX_EMAILS_PER_HOUR", "50"))
    if mode == ReleaseMode.PRODUCTION and max_emails_hour > 100:
        print(f"[PRODUCTION][HIGH_VOLUME_WARNING] MAX_EMAILS_PER_HOUR={max_emails_hour} - consider lower values for warm-up")
    
    if email_mode == "SENDGRID":
        if not os.getenv("SENDGRID_API_KEY"):
            print("[DRY_RUN_FALLBACK] SENDGRID_API_KEY not set - will use DRY_RUN")
        else:
            print("[EMAIL][STARTUP] SendGrid configured and ready")
    elif email_mode == "SMTP":
        if not smtp_configured:
            missing = []
            if not os.getenv("SMTP_HOST"): missing.append("SMTP_HOST")
            if not os.getenv("SMTP_USERNAME"): missing.append("SMTP_USERNAME")
            if not os.getenv("SMTP_PASSWORD"): missing.append("SMTP_PASSWORD")
            if not os.getenv("SMTP_FROM_EMAIL"): missing.append("SMTP_FROM_EMAIL")
            print(f"[DRY_RUN_FALLBACK] SMTP missing: {', '.join(missing)} - will use DRY_RUN")
        else:
            print("[EMAIL][STARTUP] SMTP configured and ready")
    
    apollo_key = os.getenv("APOLLO_API_KEY")
    if apollo_key:
        print("[LEADS][STARTUP] Apollo.io configured - lead generation ACTIVE")
    else:
        print("[LEADS][STARTUP] Apollo.io NOT configured - lead generation PAUSED")
        print("[LEADS][STARTUP] Connect Apollo via admin console or set APOLLO_API_KEY")


def get_throttle_defaults() -> Dict[str, int]:
    """
    Get default throttle values based on release mode.
    
    Returns sensible defaults that can be overridden by env vars.
    SANDBOX and PRODUCTION both use the same defaults for simplicity.
    """
    mode = get_release_mode()
    
    if mode == ReleaseMode.PRODUCTION:
        return {
            "max_emails_per_cycle": 10,
            "max_emails_per_hour": 50,
            "max_new_leads_per_cycle": 10
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
