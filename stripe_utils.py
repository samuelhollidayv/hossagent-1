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
MAX_STRIPE_LOG_ENTRIES = 5000  # Capped for log rotation


def get_min_invoice_cents() -> int:
    """Get minimum invoice amount from env or default ($1.00)."""
    try:
        return int(os.getenv("STRIPE_MIN_AMOUNT_CENTS", "100"))
    except ValueError:
        return 100


def get_max_invoice_cents() -> int:
    """Get maximum invoice amount from env or default ($500.00)."""
    try:
        return int(os.getenv("STRIPE_MAX_AMOUNT_CENTS", "50000"))
    except ValueError:
        return 50000


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
    
    Uses env variables STRIPE_MIN_AMOUNT_CENTS and STRIPE_MAX_AMOUNT_CENTS
    for configurable limits. Defaults to $1.00 - $500.00.
    
    Returns:
        (is_valid, message)
    """
    min_cents = get_min_invoice_cents()
    max_cents = get_max_invoice_cents()
    
    if amount_cents < min_cents:
        msg = f"Amount ${amount_cents/100:.2f} below minimum ${min_cents/100:.2f}"
        print(f"[STRIPE][AMOUNT_OUT_OF_RANGE] {msg}")
        return False, msg
    
    if amount_cents > max_cents:
        msg = f"Amount ${amount_cents/100:.2f} above maximum ${max_cents/100:.2f}"
        print(f"[STRIPE][AMOUNT_OUT_OF_RANGE] {msg}")
        return False, msg
    
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
        if not api_key:
            return PaymentLinkResult(
                success=False,
                payment_url=None,
                stripe_id=None,
                error="STRIPE_API_KEY not set",
                mode="stripe"
            )
        
        currency = get_default_currency()
        
        price_response = requests.post(
            "https://api.stripe.com/v1/prices",
            auth=(str(api_key), ""),
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
            auth=(str(api_key), ""),
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
    min_cents = get_min_invoice_cents()
    max_cents = get_max_invoice_cents()
    
    recent_log = get_stripe_log(5)
    last_webhook_event = None
    last_error = None
    for entry in reversed(recent_log):
        if "webhook" in entry.get("event_type", ""):
            last_webhook_event = entry.get("timestamp")
            break
        if "error" in entry.get("event_type", "") or "failed" in entry.get("event_type", ""):
            if not last_error:
                last_error = entry.get("data", {}).get("error")
    
    return {
        "enabled": is_enabled,
        "configured": is_valid,
        "message": message,
        "currency": get_default_currency(),
        "webhook_configured": webhook_secret is not None and len(webhook_secret) > 0,
        "min_amount": f"${min_cents/100:.2f}",
        "max_amount": f"${max_cents/100:.2f}",
        "last_webhook_event": last_webhook_event,
        "last_error": last_error
    }


def validate_stripe_at_startup() -> None:
    """
    Validate Stripe configuration at startup and print status banner.
    Called from main.py during app initialization.
    
    Logs presence/absence of keys without exposing values.
    """
    is_enabled = is_stripe_enabled()
    api_key = get_stripe_api_key()
    webhook_secret = get_stripe_webhook_secret()
    
    api_key_present = api_key is not None and len(api_key) > 0
    webhook_secret_present = webhook_secret is not None and len(webhook_secret) > 0
    
    if is_enabled:
        if api_key_present:
            print(f"[STRIPE][STARTUP] Stripe ENABLED - API key present, webhook secret {'present' if webhook_secret_present else 'NOT SET'}")
            print(f"[STRIPE][STARTUP] Currency: {get_default_currency().upper()}, Limits: ${get_min_invoice_cents()/100:.2f}-${get_max_invoice_cents()/100:.2f}")
        else:
            print(f"[STRIPE][STARTUP][WARNING] ENABLE_STRIPE=TRUE but STRIPE_API_KEY is missing - falling back to DRY_RUN")
    else:
        print(f"[STRIPE][STARTUP] Stripe disabled (ENABLE_STRIPE != TRUE)")


def ensure_invoice_payment_url(
    invoice_id: int,
    amount_cents: int,
    customer_id: int,
    customer_email: str,
    customer_company: str,
    invoice_status: str,
    existing_payment_url: Optional[str]
) -> PaymentLinkResult:
    """
    Ensure an invoice has a Stripe payment link if Stripe is enabled.
    
    Used for retroactive payment link generation on existing invoices.
    
    Conditions to skip:
    - ENABLE_STRIPE is FALSE
    - Stripe credentials are invalid
    - Invoice status is 'paid'
    - Invoice already has a payment_url
    
    Args:
        invoice_id: HossAgent invoice ID
        amount_cents: Invoice amount in cents
        customer_id: Customer ID
        customer_email: Customer email for Stripe
        customer_company: Customer company name for description
        invoice_status: Current invoice status
        existing_payment_url: Current payment_url (if any)
    
    Returns:
        PaymentLinkResult with success status and payment URL
    """
    if not is_stripe_enabled():
        return PaymentLinkResult(
            success=False,
            payment_url=None,
            stripe_id=None,
            error="Stripe disabled",
            mode="dry_run"
        )
    
    is_valid, config_msg = validate_stripe_config()
    if not is_valid:
        print(f"[STRIPE][DRY_RUN_FALLBACK] {config_msg}")
        log_stripe_event("ensure_link_dry_run", {
            "reason": config_msg,
            "invoice_id": invoice_id
        })
        return PaymentLinkResult(
            success=False,
            payment_url=None,
            stripe_id=None,
            error=f"DRY_RUN_FALLBACK: {config_msg}",
            mode="dry_run"
        )
    
    if invoice_status == "paid":
        return PaymentLinkResult(
            success=False,
            payment_url=None,
            stripe_id=None,
            error="Invoice already paid",
            mode="stripe"
        )
    
    if existing_payment_url and len(existing_payment_url) > 10:
        return PaymentLinkResult(
            success=False,
            payment_url=existing_payment_url,
            stripe_id=None,
            error="Payment URL already exists",
            mode="stripe"
        )
    
    result = create_payment_link(
        amount_cents=amount_cents,
        customer_id=customer_id,
        customer_email=customer_email,
        description=f"Invoice #{invoice_id} - {customer_company}",
        invoice_id=invoice_id
    )
    
    if result.success:
        print(f"[STRIPE][LINK_CREATED] Invoice {invoice_id} amount=${amount_cents/100:.2f}")
        log_stripe_event("retroactive_link_created", {
            "invoice_id": invoice_id,
            "amount_cents": amount_cents,
            "payment_url": result.payment_url
        })
    
    return result


def get_invoice_payment_stats(invoices: list) -> Dict[str, Any]:
    """
    Get payment link statistics for a list of invoices.
    
    Args:
        invoices: List of invoice objects with payment_url and status attributes
    
    Returns:
        Dict with counts of invoices with/without payment links
    """
    total = len(invoices)
    with_payment_url = 0
    without_payment_url = 0
    paid = 0
    unpaid_without_link = 0
    
    for inv in invoices:
        status = getattr(inv, 'status', 'draft')
        payment_url = getattr(inv, 'payment_url', None)
        
        if status == 'paid':
            paid += 1
        elif payment_url and len(payment_url) > 10:
            with_payment_url += 1
        else:
            without_payment_url += 1
            if status in ('draft', 'sent'):
                unpaid_without_link += 1
    
    return {
        "total": total,
        "paid": paid,
        "with_payment_url": with_payment_url,
        "without_payment_url": without_payment_url,
        "unpaid_without_link": unpaid_without_link
    }


def get_stripe_payment_mode_status() -> Dict[str, Any]:
    """
    Get comprehensive Stripe payment mode status for templates.
    
    Returns:
        Dict with:
        - payments_enabled: True if Stripe is fully configured
        - payments_available: True if payments can be processed
        - status_message: Human-readable status message
        - show_pay_buttons: Whether to show PAY NOW buttons
    """
    is_enabled = is_stripe_enabled()
    is_valid, message = validate_stripe_config()
    
    if not is_enabled:
        return {
            "payments_enabled": False,
            "payments_available": False,
            "status_message": "Online payments are currently disabled. Contact your operator for payment options.",
            "show_pay_buttons": False
        }
    
    if not is_valid:
        return {
            "payments_enabled": True,
            "payments_available": False,
            "status_message": "Online payments temporarily unavailable.",
            "show_pay_buttons": False
        }
    
    return {
        "payments_enabled": True,
        "payments_available": True,
        "status_message": "",
        "show_pay_buttons": True
    }
