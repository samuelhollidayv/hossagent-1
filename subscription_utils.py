"""
Subscription Management for HossAgent SaaS.

Handles:
- Trial/paid plan gating
- Usage limits tracking
- Stripe product/price bootstrap
- Customer upgrade flow
- Trial abuse prevention

Environment Variables:
  STRIPE_PRICE_ID - Existing price ID (optional, auto-created if missing)
  STRIPE_PRODUCT_ID - Existing product ID (optional, auto-created if missing)

Plan Rules:
  trial: 7 days, 15 tasks, 20 leads, DRY_RUN email, no billing, no autopilot
  paid: Unlimited, real email, full billing, autopilot enabled
  trial_expired: Locked account, upgrade required
"""
import os
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from sqlmodel import Session, select

from models import Customer, TrialIdentity, TRIAL_TASK_LIMIT, TRIAL_LEAD_LIMIT, TRIAL_DAYS


@dataclass
class PlanStatus:
    """Current plan status for a customer."""
    plan: str  # trial, paid, trial_expired
    is_trial: bool
    is_paid: bool
    is_expired: bool
    days_remaining: int
    tasks_used: int
    tasks_limit: int
    leads_used: int
    leads_limit: int
    can_run_tasks: bool
    can_generate_leads: bool
    can_send_real_email: bool
    can_use_billing: bool
    can_use_autopilot: bool
    upgrade_required: bool
    status_message: str


def get_customer_plan_status(customer: Customer) -> PlanStatus:
    """
    Get complete plan status for a customer.
    
    Determines all feature access based on plan and usage.
    """
    now = datetime.utcnow()
    plan = customer.plan or "trial"
    
    if plan == "paid":
        return PlanStatus(
            plan="paid",
            is_trial=False,
            is_paid=True,
            is_expired=False,
            days_remaining=999,
            tasks_used=customer.tasks_this_period or 0,
            tasks_limit=999999,
            leads_used=customer.leads_this_period or 0,
            leads_limit=999999,
            can_run_tasks=True,
            can_generate_leads=True,
            can_send_real_email=True,
            can_use_billing=True,
            can_use_autopilot=True,
            upgrade_required=False,
            status_message="Full access - $99/month subscription active"
        )
    
    trial_end = customer.trial_end_at
    if trial_end is None:
        if customer.trial_start_at:
            trial_end = customer.trial_start_at + timedelta(days=TRIAL_DAYS)
        else:
            trial_end = now + timedelta(days=TRIAL_DAYS)
    
    days_remaining = max(0, (trial_end - now).days)
    is_expired = now >= trial_end or plan == "trial_expired"
    
    tasks_used = customer.tasks_this_period or 0
    leads_used = customer.leads_this_period or 0
    can_run_tasks = tasks_used < TRIAL_TASK_LIMIT and not is_expired
    can_generate_leads = leads_used < TRIAL_LEAD_LIMIT and not is_expired
    
    if is_expired:
        return PlanStatus(
            plan="trial_expired",
            is_trial=False,
            is_paid=False,
            is_expired=True,
            days_remaining=0,
            tasks_used=tasks_used,
            tasks_limit=TRIAL_TASK_LIMIT,
            leads_used=leads_used,
            leads_limit=TRIAL_LEAD_LIMIT,
            can_run_tasks=False,
            can_generate_leads=False,
            can_send_real_email=False,
            can_use_billing=False,
            can_use_autopilot=False,
            upgrade_required=True,
            status_message="Trial expired - Upgrade to continue"
        )
    
    task_warning = f"{tasks_used}/{TRIAL_TASK_LIMIT} tasks used"
    lead_warning = f"{leads_used}/{TRIAL_LEAD_LIMIT} leads used"
    
    return PlanStatus(
        plan="trial",
        is_trial=True,
        is_paid=False,
        is_expired=False,
        days_remaining=days_remaining,
        tasks_used=tasks_used,
        tasks_limit=TRIAL_TASK_LIMIT,
        leads_used=leads_used,
        leads_limit=TRIAL_LEAD_LIMIT,
        can_run_tasks=can_run_tasks,
        can_generate_leads=can_generate_leads,
        can_send_real_email=False,
        can_use_billing=False,
        can_use_autopilot=False,
        upgrade_required=False,
        status_message=f"Trial Mode - {days_remaining} days remaining ({task_warning}, {lead_warning})"
    )


def check_trial_abuse(
    session: Session,
    email: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_fingerprint: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Check if a new trial signup would be abuse.
    
    Returns:
        (is_allowed, block_reason) - (True, None) if allowed, (False, reason) if blocked
    """
    user_agent_hash = None
    if user_agent:
        user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:32]
    
    email_match = session.exec(
        select(TrialIdentity).where(TrialIdentity.email == email.lower())
    ).first()
    if email_match:
        return False, f"Email already used for trial: {email}"
    
    if ip_address:
        ip_match = session.exec(
            select(TrialIdentity).where(TrialIdentity.ip_address == ip_address)
        ).first()
        if ip_match:
            return False, f"IP address already used for trial"
    
    if device_fingerprint:
        fp_match = session.exec(
            select(TrialIdentity).where(TrialIdentity.device_fingerprint == device_fingerprint)
        ).first()
        if fp_match:
            return False, "Device already used for trial"
    
    return True, None


def record_trial_identity(
    session: Session,
    customer_id: int,
    email: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_fingerprint: Optional[str] = None,
    blocked: bool = False,
    block_reason: Optional[str] = None
) -> TrialIdentity:
    """
    Record trial identity for abuse prevention.
    """
    user_agent_hash = None
    if user_agent:
        user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:32]
    
    identity = TrialIdentity(
        email=email.lower(),
        ip_address=ip_address,
        user_agent_hash=user_agent_hash,
        device_fingerprint=device_fingerprint,
        customer_id=customer_id,
        blocked=blocked,
        block_reason=block_reason
    )
    session.add(identity)
    return identity


def initialize_trial(customer: Customer) -> Customer:
    """
    Initialize a customer with trial plan.
    Sets trial start/end dates and resets usage counters.
    """
    now = datetime.utcnow()
    customer.plan = "trial"
    customer.trial_start_at = now
    customer.trial_end_at = now + timedelta(days=TRIAL_DAYS)
    customer.subscription_status = "none"
    customer.tasks_this_period = 0
    customer.leads_this_period = 0
    return customer


def upgrade_to_paid(customer: Customer, stripe_subscription_id: Optional[str] = None) -> Customer:
    """
    Upgrade a customer to paid plan.
    """
    customer.plan = "paid"
    customer.subscription_status = "active"
    if stripe_subscription_id:
        customer.stripe_subscription_id = stripe_subscription_id
    customer.tasks_this_period = 0
    customer.leads_this_period = 0
    return customer


def expire_trial(customer: Customer) -> Customer:
    """
    Mark a customer's trial as expired.
    """
    customer.plan = "trial_expired"
    customer.subscription_status = "none"
    return customer


def increment_task_usage(session: Session, customer_id: int) -> bool:
    """
    Increment task usage for a customer (with blocking check).
    Returns True if task can proceed, False if limit reached.
    
    Note: Use increment_tasks_used() for soft-cap (display only) incrementing.
    """
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    
    if not customer:
        return False
    
    status = get_customer_plan_status(customer)
    if not status.can_run_tasks:
        print(f"[SUBSCRIPTION] Customer {customer_id} cannot run tasks: {status.status_message}")
        return False
    
    customer.tasks_this_period = (customer.tasks_this_period or 0) + 1
    session.add(customer)
    return True


def increment_lead_usage(session: Session, customer_id: int) -> bool:
    """
    Increment lead usage for a customer (with blocking check).
    Returns True if lead can proceed, False if limit reached.
    
    Note: Use increment_leads_used() for soft-cap (display only) incrementing.
    """
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    
    if not customer:
        return False
    
    status = get_customer_plan_status(customer)
    if not status.can_generate_leads:
        print(f"[SUBSCRIPTION] Customer {customer_id} cannot generate leads: {status.status_message}")
        return False
    
    customer.leads_this_period = (customer.leads_this_period or 0) + 1
    session.add(customer)
    return True


def increment_tasks_used(session: Session, customer_id: int) -> bool:
    """
    Increment task usage counter for a customer (soft cap - display only).
    
    This function ALWAYS increments the counter regardless of limits.
    Soft caps are enforced at the UI layer for display only.
    
    Returns True if increment was successful, False if customer not found.
    Safe to call multiple times - handles missing customers gracefully.
    """
    if not customer_id:
        return False
    
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    
    if not customer:
        print(f"[USAGE] Customer {customer_id} not found for task increment")
        return False
    
    old_count = customer.tasks_this_period or 0
    customer.tasks_this_period = old_count + 1
    session.add(customer)
    
    status = get_customer_plan_status(customer)
    if status.is_trial and customer.tasks_this_period > status.tasks_limit:
        print(f"[USAGE][SOFT_CAP] Customer {customer_id} exceeded task limit: {customer.tasks_this_period}/{status.tasks_limit}")
    else:
        print(f"[USAGE] Customer {customer_id} tasks: {customer.tasks_this_period}/{status.tasks_limit if status.is_trial else 'unlimited'}")
    
    return True


def increment_leads_used(session: Session, customer_id: int) -> bool:
    """
    Increment lead usage counter for a customer (soft cap - display only).
    
    This function ALWAYS increments the counter regardless of limits.
    Soft caps are enforced at the UI layer for display only.
    
    Returns True if increment was successful, False if customer not found.
    Safe to call multiple times - handles missing customers gracefully.
    """
    if not customer_id:
        return False
    
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    
    if not customer:
        print(f"[USAGE] Customer {customer_id} not found for lead increment")
        return False
    
    old_count = customer.leads_this_period or 0
    customer.leads_this_period = old_count + 1
    session.add(customer)
    
    status = get_customer_plan_status(customer)
    if status.is_trial and customer.leads_this_period > status.leads_limit:
        print(f"[USAGE][SOFT_CAP] Customer {customer_id} exceeded lead limit: {customer.leads_this_period}/{status.leads_limit}")
    else:
        print(f"[USAGE] Customer {customer_id} leads: {customer.leads_this_period}/{status.leads_limit if status.is_trial else 'unlimited'}")
    
    return True


def get_stripe_product_id() -> Optional[str]:
    """Get Stripe product ID from environment."""
    return os.getenv("STRIPE_PRODUCT_ID")


def get_stripe_price_id() -> Optional[str]:
    """Get Stripe price ID from environment."""
    return os.getenv("STRIPE_PRICE_ID")


def set_stripe_product_id(product_id: str) -> None:
    """Store Stripe product ID (in-memory for this session)."""
    os.environ["STRIPE_PRODUCT_ID"] = product_id


def set_stripe_price_id(price_id: str) -> None:
    """Store Stripe price ID (in-memory for this session)."""
    os.environ["STRIPE_PRICE_ID"] = price_id


def bootstrap_stripe_subscription_product() -> Dict[str, Any]:
    """
    Bootstrap Stripe product and price for subscription.
    Creates them if they don't exist.
    
    Returns:
        Dict with product_id, price_id, and status
    """
    from stripe_utils import is_stripe_enabled, get_stripe_api_key
    import requests
    
    result = {
        "success": False,
        "product_id": None,
        "price_id": None,
        "message": "",
        "created_product": False,
        "created_price": False
    }
    
    if not is_stripe_enabled():
        result["message"] = "Stripe disabled - skipping product bootstrap"
        return result
    
    api_key = get_stripe_api_key()
    if not api_key:
        result["message"] = "No Stripe API key - skipping product bootstrap"
        return result
    
    existing_product_id = get_stripe_product_id()
    existing_price_id = get_stripe_price_id()
    
    if existing_product_id and existing_price_id:
        result["success"] = True
        result["product_id"] = existing_product_id
        result["price_id"] = existing_price_id
        result["message"] = f"Using existing Stripe product ...{existing_product_id[-4:]} and price ...{existing_price_id[-4:]}"
        print(f"[STRIPE][SUBSCRIPTION] {result['message']}")
        return result
    
    try:
        product_id = existing_product_id
        if not product_id:
            product_response = requests.post(
                "https://api.stripe.com/v1/products",
                auth=(str(api_key), ""),
                data={
                    "name": "HossAgent Subscription",
                    "description": "Full access to HossAgent autonomous business engine - $99/month"
                },
                timeout=30
            )
            
            if product_response.status_code != 200:
                result["message"] = f"Failed to create Stripe product: {product_response.text[:100]}"
                print(f"[STRIPE][SUBSCRIPTION][ERROR] {result['message']}")
                return result
            
            product_data = product_response.json()
            product_id = product_data["id"]
            set_stripe_product_id(product_id)
            result["created_product"] = True
            print(f"[STRIPE][SUBSCRIPTION] Created product: ...{product_id[-4:]}")
        
        price_id = existing_price_id
        if not price_id:
            price_response = requests.post(
                "https://api.stripe.com/v1/prices",
                auth=(str(api_key), ""),
                data={
                    "product": product_id,
                    "unit_amount": 9900,  # $99.00
                    "currency": "usd",
                    "recurring[interval]": "month"
                },
                timeout=30
            )
            
            if price_response.status_code != 200:
                result["message"] = f"Failed to create Stripe price: {price_response.text[:100]}"
                print(f"[STRIPE][SUBSCRIPTION][ERROR] {result['message']}")
                return result
            
            price_data = price_response.json()
            price_id = price_data["id"]
            set_stripe_price_id(price_id)
            result["created_price"] = True
            print(f"[STRIPE][SUBSCRIPTION] Created price: ...{price_id[-4:]}")
        
        result["success"] = True
        result["product_id"] = product_id
        result["price_id"] = price_id
        result["message"] = f"Stripe subscription ready: product ...{product_id[-4:]}, price ...{price_id[-4:]}"
        print(f"[STRIPE][SUBSCRIPTION] {result['message']}")
        return result
        
    except Exception as e:
        result["message"] = f"Error bootstrapping Stripe: {str(e)}"
        print(f"[STRIPE][SUBSCRIPTION][ERROR] {result['message']}")
        return result


def create_stripe_customer(
    customer_id: int,
    email: str,
    company: str
) -> Tuple[Optional[str], Optional[str]]:
    """
    Create a Stripe customer if needed.
    
    Returns:
        (stripe_customer_id, error)
    """
    from stripe_utils import is_stripe_enabled, get_stripe_api_key
    import requests
    
    if not is_stripe_enabled():
        return None, "Stripe disabled"
    
    api_key = get_stripe_api_key()
    if not api_key:
        return None, "No Stripe API key"
    
    try:
        response = requests.post(
            "https://api.stripe.com/v1/customers",
            auth=(str(api_key), ""),
            data={
                "email": email,
                "name": company,
                "metadata[hossagent_customer_id]": str(customer_id)
            },
            timeout=30
        )
        
        if response.status_code != 200:
            return None, f"Failed to create Stripe customer: {response.text[:100]}"
        
        data = response.json()
        stripe_customer_id = data["id"]
        print(f"[STRIPE][CUSTOMER] Created Stripe customer ...{stripe_customer_id[-4:]} for HossAgent customer {customer_id}")
        return stripe_customer_id, None
        
    except Exception as e:
        return None, str(e)


def create_subscription(
    stripe_customer_id: str,
    customer_id: int
) -> Tuple[Optional[str], Optional[str]]:
    """
    Create a Stripe subscription for a customer.
    
    Returns:
        (subscription_id, error)
    """
    from stripe_utils import is_stripe_enabled, get_stripe_api_key
    import requests
    
    if not is_stripe_enabled():
        return None, "Stripe disabled"
    
    api_key = get_stripe_api_key()
    if not api_key:
        return None, "No Stripe API key"
    
    price_id = get_stripe_price_id()
    if not price_id:
        return None, "No Stripe price ID - run bootstrap first"
    
    try:
        response = requests.post(
            "https://api.stripe.com/v1/subscriptions",
            auth=(str(api_key), ""),
            data={
                "customer": stripe_customer_id,
                "items[0][price]": price_id,
                "metadata[hossagent_customer_id]": str(customer_id)
            },
            timeout=30
        )
        
        if response.status_code != 200:
            return None, f"Failed to create subscription: {response.text[:100]}"
        
        data = response.json()
        subscription_id = data["id"]
        print(f"[STRIPE][SUBSCRIPTION] Created subscription ...{subscription_id[-4:]} for customer {customer_id}")
        return subscription_id, None
        
    except Exception as e:
        return None, str(e)


def get_subscription_status() -> Dict[str, Any]:
    """Get current subscription configuration status for admin display."""
    from stripe_utils import is_stripe_enabled, get_stripe_api_key, get_stripe_webhook_secret
    
    product_id = get_stripe_product_id()
    price_id = get_stripe_price_id()
    api_key = get_stripe_api_key()
    webhook_secret = get_stripe_webhook_secret()
    
    return {
        "enabled": is_stripe_enabled(),
        "api_key_present": api_key is not None and len(api_key) > 0,
        "webhook_secret_present": webhook_secret is not None and len(webhook_secret) > 0,
        "product_id": f"...{product_id[-4:]}" if product_id else None,
        "price_id": f"...{price_id[-4:]}" if price_id else None,
        "product_ready": product_id is not None,
        "price_ready": price_id is not None,
        "subscription_price": "$99/month",
        "trial_days": TRIAL_DAYS,
        "trial_task_limit": TRIAL_TASK_LIMIT,
        "trial_lead_limit": TRIAL_LEAD_LIMIT
    }


def should_force_email_dry_run(customer: Customer) -> bool:
    """
    Check if email should be forced to DRY_RUN for this customer.
    Trial users always get DRY_RUN.
    """
    status = get_customer_plan_status(customer)
    return not status.can_send_real_email


def should_disable_billing_for_customer(customer: Customer) -> bool:
    """
    Check if billing should be disabled for this customer.
    Trial users don't get Stripe billing.
    """
    status = get_customer_plan_status(customer)
    return not status.can_use_billing


def should_disable_autopilot_for_customer(customer: Customer) -> bool:
    """
    Check if autopilot should be disabled for this customer.
    Trial users don't get autopilot.
    """
    status = get_customer_plan_status(customer)
    return not status.can_use_autopilot


def get_stripe_price_id_pro() -> Optional[str]:
    """Get the Stripe Price ID for the Pro subscription plan."""
    price_id = os.getenv("STRIPE_PRICE_ID_PRO")
    if price_id:
        return price_id
    return get_stripe_price_id()


def get_or_create_subscription_checkout_link(
    customer: Customer,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None
) -> Tuple[bool, Optional[str], str, Optional[str]]:
    """
    Get or create a Stripe Checkout session URL for subscription.
    
    Args:
        customer: The customer to create checkout for
        success_url: URL to redirect after successful payment
        cancel_url: URL to redirect if payment cancelled
    
    Returns:
        (success, url, mode, error)
        - success: True if URL is available
        - url: The checkout URL or None
        - mode: 'live', 'dry_run', or 'disabled'
        - error: Error message if any
    """
    from stripe_utils import is_stripe_enabled, get_stripe_api_key
    import requests
    
    plan_status = get_customer_plan_status(customer)
    if plan_status.is_paid:
        return False, None, "already_paid", "Customer already has an active subscription"
    
    if not is_stripe_enabled():
        print(f"[STRIPE][SUBSCRIPTION][DRY_RUN_FALLBACK] Stripe disabled for customer {customer.id}")
        return False, None, "disabled", "Online billing not configured"
    
    api_key = get_stripe_api_key()
    if not api_key:
        print(f"[STRIPE][SUBSCRIPTION][DRY_RUN_FALLBACK] No API key for customer {customer.id}")
        return False, None, "disabled", "Stripe API key not configured"
    
    price_id = get_stripe_price_id_pro()
    if not price_id:
        print(f"[STRIPE][SUBSCRIPTION][DRY_RUN_FALLBACK] No price ID for customer {customer.id}")
        return False, None, "disabled", "Subscription price not configured"
    
    if not success_url:
        success_url = f"/portal/{customer.public_token}?payment=success"
    if not cancel_url:
        cancel_url = f"/portal/{customer.public_token}?payment=cancelled"
    
    try:
        stripe_customer_id = customer.stripe_customer_id
        if not stripe_customer_id:
            stripe_customer_id, err = create_stripe_customer(
                customer_id=customer.id,
                email=customer.contact_email,
                company=customer.company
            )
            if err:
                return False, None, "error", f"Failed to create Stripe customer: {err}"
        
        response = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(str(api_key), ""),
            data={
                "customer": stripe_customer_id,
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": 1,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "metadata[hossagent_customer_id]": str(customer.id),
                "metadata[public_token]": customer.public_token or "",
                "subscription_data[metadata][hossagent_customer_id]": str(customer.id)
            },
            timeout=30
        )
        
        if response.status_code != 200:
            error_text = response.text[:200]
            print(f"[STRIPE][SUBSCRIPTION][ERROR] Checkout creation failed: {error_text}")
            return False, None, "error", f"Failed to create checkout: {error_text}"
        
        data = response.json()
        checkout_url = data.get("url")
        session_id = data.get("id")
        
        print(f"[STRIPE][SUBSCRIPTION] Created checkout session {session_id[-8:]} for customer {customer.id}")
        return True, checkout_url, "live", None
        
    except Exception as e:
        error_msg = str(e)
        print(f"[STRIPE][SUBSCRIPTION][ERROR] Exception creating checkout: {error_msg}")
        return False, None, "error", error_msg


def create_billing_portal_link(
    customer: Customer,
    return_url: Optional[str] = None
) -> Tuple[bool, Optional[str], str, Optional[str]]:
    """
    Create a Stripe Customer Portal link for managing billing.
    
    Args:
        customer: The customer
        return_url: URL to return to after portal session
    
    Returns:
        (success, url, mode, error)
    """
    from stripe_utils import is_stripe_enabled, get_stripe_api_key
    import requests
    
    if not is_stripe_enabled():
        return False, None, "disabled", "Stripe not configured"
    
    api_key = get_stripe_api_key()
    if not api_key:
        return False, None, "disabled", "No Stripe API key"
    
    stripe_customer_id = customer.stripe_customer_id
    if not stripe_customer_id:
        return False, None, "error", "No Stripe customer ID"
    
    if not return_url:
        return_url = f"/portal/{customer.public_token}"
    
    try:
        response = requests.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            auth=(str(api_key), ""),
            data={
                "customer": stripe_customer_id,
                "return_url": return_url
            },
            timeout=30
        )
        
        if response.status_code != 200:
            error_text = response.text[:200]
            print(f"[STRIPE][PORTAL][ERROR] Portal creation failed: {error_text}")
            return False, None, "error", f"Failed to create portal: {error_text}"
        
        data = response.json()
        portal_url = data.get("url")
        
        print(f"[STRIPE][PORTAL] Created billing portal for customer {customer.id}")
        return True, portal_url, "live", None
        
    except Exception as e:
        return False, None, "error", str(e)
