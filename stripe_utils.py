"""
Stripe Billing Integration for HossAgent.
Handles payment link creation and webhook processing.

Environment Variables:
  ENABLE_STRIPE = TRUE/FALSE (default: FALSE)
  STRIPE_API_KEY - Stripe secret key (sk_...)
  STRIPE_WEBHOOK_SECRET - Webhook signing secret (whsec_...)
  STRIPE_DEFAULT_CURRENCY - Currency code (default: usd)

Safety:
  - Falls back to DRY_RUN if credentials missing
  - Invoice amount safety clamp: $1-$500 by default
  - All errors are caught and logged without crashing
"""
import os
import json
import hmac
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PaymentLinkResult:
    success: bool
    payment_url: Optional[str]
    stripe_id: Optional[str]
    error: Optional[str]
    mode: str  # "stripe" or "dry_run"


STRIPE_LOG_FILE = Path("stripe_events.json")
MAX_STRIPE_LOG_ENTRIES = 500

MIN_INVOICE_CENTS = 100
MAX_INVOICE_CENTS = 50000


def is_stripe_enabled() -> bool:
    """Check if Stripe is enabled via environment variable."""
    return os.getenv("ENABLE_STRIPE", "FALSE").upper() == "TRUE"


def get_stripe_api_key() -> Optional[str]:
    """Get Stripe API key from environment."""
    return os.getenv("STRIPE_API_KEY")


def get_stripe_webhook_secret() -> Optional[str]:
    """Get Stripe webhook secret from environment."""
    return os.getenv("STRIPE_WEBHOOK_SECRET")


def get_default_currency() -> str:
    """Get default currency from environment."""
    return os.getenv("STRIPE_DEFAULT_CURRENCY", "usd").lower()


def validate_stripe_config() -> Tuple[bool, str]:
    """
    Validate Stripe configuration.
    
    Returns:
        (is_valid, message)
    """
    if not is_stripe_enabled():
        return False, "Stripe disabled (ENABLE_STRIPE != TRUE)"
    
    api_key = get_stripe_api_key()
    if not api_key:
        return False, "STRIPE_API_KEY not set"
    
    if not api_key.startswith("sk_"):
        return False, "STRIPE_API_KEY should start with 'sk_'"
    
    return True, "Stripe configured"


def _load_stripe_log() -> list:
    """Load Stripe event log."""
    try:
        if STRIPE_LOG_FILE.exists():
            with open(STRIPE_LOG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_stripe_log(entries: list) -> None:
    """Save Stripe event log."""
    try:
        entries = entries[-MAX_STRIPE_LOG_ENTRIES:]
        with open(STRIPE_LOG_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        print(f"[STRIPE] Warning: Could not save event log: {e}")


def log_stripe_event(event_type: str, data: Dict[str, Any]) -> None:
    """Log a Stripe event for admin visibility."""
    entries = _load_stripe_log()
    entries.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "data": data
    })
    _save_stripe_log(entries)


def get_stripe_log(limit: int = 20) -> list:
    """Get recent Stripe events for admin display."""
    entries = _load_stripe_log()
    return entries[-limit:]


def check_invoice_amount(amount_cents: int) -> Tuple[bool, str]:
    """
    Check if invoice amount is within safety bounds.
    
    Returns:
        (is_valid, message)
    """
    if amount_cents < MIN_INVOICE_CENTS:
        return False, f"Amount ${amount_cents/100:.2f} below minimum ${MIN_INVOICE_CENTS/100:.2f}"
    
    if amount_cents > MAX_INVOICE_CENTS:
        return False, f"Amount ${amount_cents/100:.2f} above maximum ${MAX_INVOICE_CENTS/100:.2f}"
    
    return True, "Amount within bounds"


def create_payment_link(
    amount_cents: int,
    customer_id: int,
    customer_email: str,
    description: str,
    invoice_id: int
) -> PaymentLinkResult:
    """
    Create a Stripe payment link for an invoice.
    
    Falls back to DRY_RUN mode if Stripe is not configured.
    Enforces amount safety bounds.
    
    Args:
        amount_cents: Amount in cents
        customer_id: HossAgent customer ID
        customer_email: Customer email for Stripe
        description: Line item description
        invoice_id: HossAgent invoice ID
    
    Returns:
        PaymentLinkResult with success status and payment URL
    """
    is_valid_config, config_msg = validate_stripe_config()
    
    if not is_valid_config:
        print(f"[STRIPE][DRY_RUN] {config_msg}")
        log_stripe_event("payment_link_dry_run", {
            "reason": config_msg,
            "invoice_id": invoice_id,
            "amount_cents": amount_cents
        })
        return PaymentLinkResult(
            success=False,
            payment_url=None,
            stripe_id=None,
            error=f"DRY_RUN: {config_msg}",
            mode="dry_run"
        )
    
    is_valid_amount, amount_msg = check_invoice_amount(amount_cents)
    if not is_valid_amount:
        print(f"[STRIPE][SKIP] {amount_msg}")
        log_stripe_event("payment_link_skipped", {
            "reason": amount_msg,
            "invoice_id": invoice_id,
            "amount_cents": amount_cents
        })
        return PaymentLinkResult(
            success=False,
            payment_url=None,
            stripe_id=None,
            error=amount_msg,
            mode="stripe"
        )
    
    try:
        import requests
        
        api_key = get_stripe_api_key()
        currency = get_default_currency()
        
        price_response = requests.post(
            "https://api.stripe.com/v1/prices",
            auth=(api_key, ""),
            data={
                "currency": currency,
                "unit_amount": amount_cents,
                "product_data[name]": description[:200]
            },
            timeout=30
        )
        
        if price_response.status_code != 200:
            error_msg = f"Price creation failed: {price_response.text[:200]}"
            print(f"[STRIPE][ERROR] {error_msg}")
            log_stripe_event("price_creation_failed", {
                "invoice_id": invoice_id,
                "error": error_msg
            })
            return PaymentLinkResult(
                success=False,
                payment_url=None,
                stripe_id=None,
                error=error_msg,
                mode="stripe"
            )
        
        price_data = price_response.json()
        price_id = price_data["id"]
        
        link_response = requests.post(
            "https://api.stripe.com/v1/payment_links",
            auth=(api_key, ""),
            data={
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": 1,
                "metadata[invoice_id]": str(invoice_id),
                "metadata[customer_id]": str(customer_id)
            },
            timeout=30
        )
        
        if link_response.status_code != 200:
            error_msg = f"Payment link creation failed: {link_response.text[:200]}"
            print(f"[STRIPE][ERROR] {error_msg}")
            log_stripe_event("payment_link_failed", {
                "invoice_id": invoice_id,
                "error": error_msg
            })
            return PaymentLinkResult(
                success=False,
                payment_url=None,
                stripe_id=None,
                error=error_msg,
                mode="stripe"
            )
        
        link_data = link_response.json()
        payment_url = link_data["url"]
        stripe_id = link_data["id"]
        
        print(f"[STRIPE] Payment link created: {stripe_id} for invoice {invoice_id}")
        log_stripe_event("payment_link_created", {
            "invoice_id": invoice_id,
            "customer_id": customer_id,
            "amount_cents": amount_cents,
            "payment_url": payment_url,
            "stripe_id": stripe_id
        })
        
        return PaymentLinkResult(
            success=True,
            payment_url=payment_url,
            stripe_id=stripe_id,
            error=None,
            mode="stripe"
        )
        
    except ImportError:
        error_msg = "requests library not available"
        print(f"[STRIPE][ERROR] {error_msg}")
        return PaymentLinkResult(
            success=False,
            payment_url=None,
            stripe_id=None,
            error=error_msg,
            mode="stripe"
        )
    except Exception as e:
        error_msg = str(e)
        print(f"[STRIPE][ERROR] Exception: {error_msg}")
        log_stripe_event("payment_link_exception", {
            "invoice_id": invoice_id,
            "error": error_msg
        })
        return PaymentLinkResult(
            success=False,
            payment_url=None,
            stripe_id=None,
            error=error_msg,
            mode="stripe"
        )


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify Stripe webhook signature.
    
    Args:
        payload: Raw request body
        signature: Stripe-Signature header value
    
    Returns:
        True if signature is valid
    """
    webhook_secret = get_stripe_webhook_secret()
    if not webhook_secret:
        print("[STRIPE][WEBHOOK] No webhook secret configured")
        return False
    
    try:
        parts = {}
        for part in signature.split(","):
            key, value = part.split("=", 1)
            parts[key] = value
        
        timestamp = parts.get("t")
        v1_signature = parts.get("v1")
        
        if not timestamp or not v1_signature:
            return False
        
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        expected_signature = hmac.new(
            webhook_secret.encode('utf-8'),
            signed_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, v1_signature)
        
    except Exception as e:
        print(f"[STRIPE][WEBHOOK] Signature verification error: {e}")
        return False


def get_stripe_status() -> Dict[str, Any]:
    """Get current Stripe configuration status for admin display."""
    is_enabled = is_stripe_enabled()
    is_valid, message = validate_stripe_config()
    webhook_secret = get_stripe_webhook_secret()
    
    return {
        "enabled": is_enabled,
        "configured": is_valid,
        "message": message,
        "currency": get_default_currency(),
        "webhook_configured": webhook_secret is not None and len(webhook_secret) > 0,
        "min_amount": f"${MIN_INVOICE_CENTS/100:.2f}",
        "max_amount": f"${MAX_INVOICE_CENTS/100:.2f}"
    }
