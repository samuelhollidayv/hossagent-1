"""
HossAgent: Autonomous AI Business Engine
FastAPI backend with autopilot-driven autonomous agents.

Routes:
- /                    → Customer Dashboard (public-facing read-only)
- /admin               → Admin Console (operator controls + autopilot toggle)
- /portal/<token>      → Customer Portal (client self-service)
- /api/leads           → List all leads (GET)
- /api/customers       → List all customers (GET)
- /api/tasks           → List all tasks (GET)
- /api/invoices        → List all invoices (GET)
- /api/run/*           → Admin endpoints to manually trigger agent cycles
- /admin/summary       → Daily/weekly summary for operators
- /admin/send-test-email → Test email configuration
- /stripe/webhook      → Stripe payment webhook
- /stripe/subscription-webhook → Stripe subscription webhook
- /upgrade             → Upgrade customer to paid plan
- /api/subscription/status → Get subscription configuration status

Subscription Model:
- Trial: 7 days, 15 tasks, 20 leads, DRY_RUN email, no billing
- Paid: $99/month, unlimited access
"""
import asyncio
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Depends, Request, HTTPException, Query, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select, func
from database import create_db_and_tables, get_session, engine
from models import Lead, Customer, Task, Invoice, SystemSettings, TrialIdentity, Signal, LeadEvent, PasswordResetToken, PendingOutbound, BusinessProfile, Report
from agents import (
    run_bizdev_cycle,
    run_onboarding_cycle,
    run_ops_cycle,
    run_billing_cycle,
    run_event_driven_bizdev_cycle,
)
from signals_agent import run_signals_agent, get_signals_summary, get_lead_events_summary, get_todays_opportunities
from email_utils import send_email, get_email_status, get_email_log
from lead_service import generate_new_leads_from_source, get_lead_source_log
from lead_sources import get_lead_source_status
from release_mode import is_release_mode, print_startup_banners, get_release_mode_status
from stripe_utils import (
    validate_stripe_at_startup,
    is_stripe_enabled,
    get_stripe_payment_mode_status,
    ensure_invoice_payment_url,
    get_invoice_payment_stats,
    process_subscription_webhook,
    verify_webhook_signature,
    log_stripe_event,
    get_stripe_webhook_secret
)
from subscription_utils import (
    get_customer_plan_status,
    get_subscription_status,
    bootstrap_stripe_subscription_product,
    create_stripe_customer,
    create_subscription,
    upgrade_to_paid,
    expire_trial,
    check_trial_abuse,
    record_trial_identity,
    initialize_trial,
    get_or_create_subscription_checkout_link,
    create_billing_portal_link
)
from auth_utils import (
    hash_password,
    verify_password,
    create_customer_session,
    verify_customer_session,
    create_admin_session,
    verify_admin_session,
    authenticate_customer,
    generate_public_token,
    get_customer_from_session,
    get_customer_from_token,
    get_admin_password,
    SESSION_COOKIE_NAME,
    ADMIN_COOKIE_NAME,
    SESSION_MAX_AGE,
    ADMIN_SESSION_MAX_AGE
)

app = FastAPI(title="HossAgent Control Engine")


# ============================================================================
# STARTUP & BACKGROUND AUTOPILOT
# ============================================================================


def run_retroactive_payment_links(max_invoices: int = 50) -> int:
    """
    Generate payment links for existing unpaid invoices that don't have them.
    
    Called on startup when ENABLE_STRIPE=TRUE to ensure all draft/sent invoices
    have payment links.
    
    Args:
        max_invoices: Maximum number of invoices to process per run (bounded)
    
    Returns:
        Number of payment links created
    """
    if not is_stripe_enabled():
        return 0
    
    from sqlmodel import Session
    
    links_created = 0
    
    try:
        with Session(engine) as session:
            invoices_needing_links = session.exec(
                select(Invoice).where(
                    (Invoice.status.in_(["draft", "sent"])) &
                    ((Invoice.payment_url == None) | (Invoice.payment_url == ""))
                ).limit(max_invoices)
            ).all()
            
            if not invoices_needing_links:
                print(f"[STRIPE][RETROACTIVE] No invoices need payment links")
                return 0
            
            print(f"[STRIPE][RETROACTIVE] Processing {len(invoices_needing_links)} invoices for payment links")
            
            for invoice in invoices_needing_links:
                customer = session.exec(
                    select(Customer).where(Customer.id == invoice.customer_id)
                ).first()
                
                if not customer:
                    print(f"[STRIPE][RETROACTIVE] Invoice {invoice.id} has no customer, skipping")
                    continue
                
                result = ensure_invoice_payment_url(
                    invoice_id=invoice.id,
                    amount_cents=invoice.amount_cents,
                    customer_id=customer.id,
                    customer_email=customer.contact_email,
                    customer_company=customer.company,
                    invoice_status=invoice.status,
                    existing_payment_url=invoice.payment_url
                )
                
                if result.success and result.payment_url:
                    invoice.payment_url = result.payment_url
                    if result.stripe_id:
                        invoice.stripe_payment_id = result.stripe_id
                    session.add(invoice)
                    links_created += 1
            
            if links_created > 0:
                session.commit()
                print(f"[STRIPE][RETROACTIVE] Created {links_created} payment links")
            
    except Exception as e:
        print(f"[STRIPE][RETROACTIVE] Error: {e}")
    
    return links_created


def bootstrap_business_profiles(session: Session) -> int:
    """
    Ensure all customers have a BusinessProfile with sensible defaults.
    
    Creates placeholder profiles for customers missing them, logging warnings.
    This enables the Contextual Opportunity Engine to function for all customers.
    
    Returns:
        Number of profiles created.
    """
    from models import BusinessProfile
    
    customers_without_profiles = session.exec(
        select(Customer).where(
            ~Customer.id.in_(
                select(BusinessProfile.customer_id)
            )
        )
    ).all()
    
    created = 0
    for customer in customers_without_profiles:
        default_profile = BusinessProfile(
            customer_id=customer.id,
            short_description=f"{customer.company} - Professional services",
            services="General business services",
            ideal_customer="Small to medium businesses",
            voice_tone="professional",
            communication_style="conversational",
            primary_contact_name=customer.contact_name or "Team",
            primary_contact_email=customer.contact_email
        )
        
        if customer.niche:
            default_profile.services = customer.niche
        if customer.geography:
            default_profile.ideal_customer = f"Businesses in {customer.geography}"
        
        session.add(default_profile)
        created += 1
        print(f"[BOOTSTRAP][WARNING] Created default BusinessProfile for customer {customer.id} ({customer.company}) - please configure in portal settings")
    
    if created > 0:
        session.commit()
        print(f"[BOOTSTRAP] Created {created} default BusinessProfiles")
    
    return created


def run_production_cleanup(session: Session, owner_email_domain: str = "", purge_all_signals: bool = True) -> dict:
    """
    One-time production database cleanup.
    
    Removes dev/test/demo data while preserving real production customers.
    This should only be run ONCE during production initialization.
    
    Args:
        session: Database session
        owner_email_domain: Domain to identify real customers (e.g., "mycompany.com")
        purge_all_signals: If True, deletes ALL signals/lead_events (fresh start)
    
    Returns:
        Summary of cleanup actions taken.
    """
    from datetime import datetime
    import re
    
    results = {
        "signals_deleted": 0,
        "lead_events_deleted": 0,
        "pending_outbound_deleted": 0,
        "reports_deleted": 0,
        "invoices_deleted": 0,
        "tasks_deleted": 0,
        "leads_deleted": 0,
        "customers_deleted": 0,
        "counters_reset": 0,
        "purged_at": datetime.utcnow().isoformat(),
        "already_run": False,
        "audit_log": []
    }
    
    cleanup_flag_file = Path("production_cleanup_completed.flag")
    if cleanup_flag_file.exists():
        results["already_run"] = True
        print("[CLEANUP] Production cleanup already completed. Skipping.")
        return results
    
    print("[CLEANUP] Starting one-time production database cleanup...")
    
    real_customer_ids = []
    fake_customer_ids = []
    all_customers = session.exec(select(Customer)).all()
    
    fake_company_patterns = [
        r"^Test\s", r"^Demo\s", r"^Fake\s", r"^Sample\s",
        r"Quantum\s*Dynamics", r"Apex\s*Ventures", r"Stratton\s*Industries",
        r"Atlas\s*Enterprise", r"Nexus\s*Capital", r"Titan\s*Logistics",
        r"Meridian\s*Solutions", r"Catalyst\s*Growth", r"Vanguard\s*Consulting",
        r"Precision\s*Demand", r"Sterling\s*Strategy", r"Forge\s*Strategic",
        r"Atlas\s*Revenue", r"Momentum\s*Marketing", r"Quantum\s*Lead",
        r"Elevate\s*Agency", r"Keystone\s*Advisory", r"Summit\s*Digital"
    ]
    
    for customer in all_customers:
        is_real = False
        is_fake = False
        
        if owner_email_domain and customer.contact_email and customer.contact_email.endswith(owner_email_domain):
            is_real = True
        
        if customer.plan == "paid" and customer.subscription_status == "active":
            is_real = True
        
        if customer.stripe_customer_id or customer.stripe_subscription_id:
            is_real = True
        
        if hasattr(customer, 'notes') and customer.notes and "ADMIN" in customer.notes.upper():
            is_real = True
        
        if customer.contact_email:
            fake_email_patterns = ["@example", "@test", "@fake", "@demo", "@localhost", "@dummy"]
            if any(p in customer.contact_email.lower() for p in fake_email_patterns):
                is_fake = True
        
        if customer.company:
            for pattern in fake_company_patterns:
                if re.search(pattern, customer.company, re.IGNORECASE):
                    is_fake = True
                    break
        
        if is_real and not is_fake:
            real_customer_ids.append(customer.id)
            print(f"[CLEANUP] Keeping real customer: {customer.id} - {customer.company} ({customer.contact_email})")
        elif is_fake and not is_real:
            fake_customer_ids.append(customer.id)
            results["audit_log"].append(f"CUSTOMER_MARKED_FAKE: {customer.id} - {customer.company}")
    
    if not real_customer_ids:
        print("[CLEANUP][WARNING] No real customers identified by domain. Checking for trial customers with real signups...")
        for customer in all_customers:
            if customer.plan == "trial" and customer.trial_start_at and customer.id not in fake_customer_ids:
                real_customer_ids.append(customer.id)
                print(f"[CLEANUP] Keeping trial customer: {customer.id} - {customer.company}")
    
    if not real_customer_ids:
        print("[CLEANUP][SAFETY] No real customers identified. Keeping all customers.")
        real_customer_ids = [c.id for c in all_customers]
    
    from models import Signal, LeadEvent, PendingOutbound, Report, Task, Invoice, Lead
    
    if purge_all_signals:
        all_signals = session.exec(select(Signal)).all()
        for s in all_signals:
            session.delete(s)
            results["signals_deleted"] += 1
        results["audit_log"].append(f"SIGNALS_PURGED_ALL: {results['signals_deleted']}")
        
        all_events = session.exec(select(LeadEvent)).all()
        for le in all_events:
            session.delete(le)
            results["lead_events_deleted"] += 1
        results["audit_log"].append(f"LEAD_EVENTS_PURGED_ALL: {results['lead_events_deleted']}")
    else:
        signals = session.exec(select(Signal)).all()
        for s in signals:
            if s.company_id and s.company_id not in real_customer_ids:
                session.delete(s)
                results["signals_deleted"] += 1
        
        lead_events = session.exec(select(LeadEvent)).all()
        for le in lead_events:
            if le.company_id and le.company_id not in real_customer_ids:
                session.delete(le)
                results["lead_events_deleted"] += 1
    
    pending = session.exec(select(PendingOutbound)).all()
    for p in pending:
        if p.customer_id not in real_customer_ids:
            session.delete(p)
            results["pending_outbound_deleted"] += 1
    results["audit_log"].append(f"PENDING_OUTBOUND_DELETED: {results['pending_outbound_deleted']}")
    
    reports = session.exec(select(Report)).all()
    for r in reports:
        if r.customer_id not in real_customer_ids:
            session.delete(r)
            results["reports_deleted"] += 1
    results["audit_log"].append(f"REPORTS_DELETED: {results['reports_deleted']}")
    
    tasks = session.exec(select(Task)).all()
    for t in tasks:
        if t.customer_id not in real_customer_ids:
            session.delete(t)
            results["tasks_deleted"] += 1
    results["audit_log"].append(f"TASKS_DELETED: {results['tasks_deleted']}")
    
    invoices = session.exec(select(Invoice)).all()
    for i in invoices:
        should_delete = False
        if i.customer_id not in real_customer_ids:
            should_delete = True
        elif i.amount_cents == 0 and i.status == "draft":
            should_delete = True
        elif i.amount_cents == 0 and not i.stripe_invoice_id:
            should_delete = True
        
        if should_delete:
            session.delete(i)
            results["invoices_deleted"] += 1
    results["audit_log"].append(f"INVOICES_DELETED: {results['invoices_deleted']}")
    
    leads = session.exec(select(Lead)).all()
    fake_lead_patterns = [
        r"^Lead_\d+",
        r"@example\.", r"@test\.", r"@fake\.", r"@demo\.",
        r"@localhost", r"@mailinator", r"@dummy",
        r"^contact@(quantum|apex|stratton|atlas|nexus|titan|meridian|catalyst|vanguard)",
        r"@atlasrevenue", r"@stratton", r"@apex", r"@quantum", r"@nexus",
        r"@titan", r"@meridian", r"@catalyst", r"@vanguard", r"@blackstone",
        r"@vector", r"@summit", r"@horizon", r"@precision", r"@sterling",
        r"@forge", r"@momentum", r"@elevate", r"@keystone", r"@pioneer",
    ]
    
    fake_company_names = [
        "atlas", "stratton", "apex", "quantum", "nexus", "titan", "meridian",
        "catalyst", "vanguard", "blackstone", "vector", "summit", "horizon",
        "precision", "sterling", "forge", "momentum", "elevate", "keystone", "pioneer"
    ]
    
    for lead in leads:
        is_fake_lead = False
        
        if hasattr(lead, 'source') and lead.source == "dummy_seed":
            is_fake_lead = True
        
        if lead.email:
            for pattern in fake_lead_patterns:
                if re.search(pattern, lead.email, re.IGNORECASE):
                    is_fake_lead = True
                    break
        
        if lead.name and re.match(r"^Lead_\d+$", lead.name):
            is_fake_lead = True
        
        if hasattr(lead, 'company') and lead.company:
            company_lower = lead.company.lower()
            for fake_name in fake_company_names:
                if fake_name in company_lower:
                    is_fake_lead = True
                    break
        
        if is_fake_lead:
            session.delete(lead)
            results["leads_deleted"] += 1
    results["audit_log"].append(f"LEADS_DELETED: {results['leads_deleted']}")
    
    for cid in fake_customer_ids:
        if cid not in real_customer_ids:
            customer = session.get(Customer, cid)
            if customer:
                session.delete(customer)
                results["customers_deleted"] += 1
                results["audit_log"].append(f"CUSTOMER_DELETED: {cid} - {customer.company}")
    
    for customer in all_customers:
        if customer.id in real_customer_ids:
            customer.tasks_this_period = 0
            customer.leads_this_period = 0
            session.add(customer)
            results["counters_reset"] += 1
    results["audit_log"].append(f"COUNTERS_RESET: {results['counters_reset']}")
    
    session.commit()
    
    audit_content = f"""Production Cleanup Audit Log
============================
Timestamp: {results['purged_at']}
Owner Domain Filter: {owner_email_domain or 'None'}
Purge All Signals: {purge_all_signals}

Summary:
--------
Signals Deleted: {results['signals_deleted']}
Lead Events Deleted: {results['lead_events_deleted']}
Pending Outbound Deleted: {results['pending_outbound_deleted']}
Reports Deleted: {results['reports_deleted']}
Tasks Deleted: {results['tasks_deleted']}
Invoices Deleted: {results['invoices_deleted']}
Leads Deleted: {results['leads_deleted']}
Customers Deleted: {results['customers_deleted']}
Counters Reset: {results['counters_reset']}

Audit Trail:
------------
""" + "\n".join(results["audit_log"])
    
    cleanup_flag_file.write_text(audit_content)
    
    print(f"[CLEANUP] Complete. Signals: {results['signals_deleted']}, Events: {results['lead_events_deleted']}, "
          f"Outbound: {results['pending_outbound_deleted']}, Reports: {results['reports_deleted']}, "
          f"Tasks: {results['tasks_deleted']}, Invoices: {results['invoices_deleted']}, "
          f"Leads: {results['leads_deleted']}, Customers: {results['customers_deleted']}, "
          f"Counters reset: {results['counters_reset']}")
    
    return results


@app.on_event("startup")
async def startup_event():
    """Initialize database, validate configuration, and start autopilot background loop."""
    print_startup_banners()
    
    create_db_and_tables()
    
    validate_stripe_at_startup()
    
    bootstrap_result = bootstrap_stripe_subscription_product()
    if bootstrap_result["success"]:
        print(f"[STARTUP] Subscription product ready: {bootstrap_result['message']}")
    elif is_stripe_enabled():
        print(f"[STARTUP] Subscription bootstrap: {bootstrap_result['message']}")
    
    run_retroactive_payment_links()
    
    asyncio.create_task(autopilot_loop())
    print("[STARTUP] HossAgent initialized. Autopilot loop active.")


async def autopilot_loop():
    """
    Background task: Runs agent cycles automatically when autopilot is enabled.
    
    Checks SystemSettings.autopilot_enabled every 15 minutes (production cycle).
    If enabled, runs the full pipeline:
      1. Lead Generation - Fetch new leads from configured source (capped by MAX_NEW_LEADS_PER_CYCLE)
      2. Signals Agent - Generate contextual intelligence from external signals
      3. BizDev - Send outreach emails to NEW leads (capped by MAX_EMAILS_PER_CYCLE)
      4. Event-Driven BizDev - Process LeadEvents for contextual outreach
      5. Onboarding - Convert qualified leads to customers
      6. Ops - Execute pending tasks and generate reports
      7. Billing - Generate invoices for completed work
    
    Per-customer autopilot settings override global behavior.
    Safe: Catches and logs exceptions without crashing the loop.
    Idempotent: Prevents duplicate LeadEvents, outbound, and reports.
    """
    # Log email mode at startup
    from email_utils import get_email_status
    email_status = get_email_status()
    print(f"[AUTOPILOT][STARTUP] Email mode: {email_status['mode']} (configured: {email_status['configured_mode']})")
    if not email_status['is_valid']:
        print(f"[AUTOPILOT][STARTUP] Email fallback reason: {email_status['message']}")
    
    while True:
        try:
            with Session(engine) as session:
                settings = session.exec(
                    select(SystemSettings).where(SystemSettings.id == 1)
                ).first()

                if settings and settings.autopilot_enabled:
                    print("\n[AUTOPILOT] Starting cycle...")
                    
                    # Bootstrap business profiles for customers without them
                    bootstrap_business_profiles(session)
                    
                    generate_new_leads_from_source(session)
                    
                    run_signals_agent(session)
                    
                    await run_bizdev_cycle(session)
                    await run_event_driven_bizdev_cycle(session)
                    await run_onboarding_cycle(session)
                    await run_ops_cycle(session)
                    await run_billing_cycle(session)
                    print("[AUTOPILOT] Cycle complete.\n")
                else:
                    pass  # Silent when disabled to reduce log noise

        except Exception as e:
            import traceback
            print(f"[AUTOPILOT][ERROR] {e}")
            print(f"[AUTOPILOT][TRACEBACK] {traceback.format_exc()}")

        # Sleep 15 minutes between cycles (production cadence)
        await asyncio.sleep(900)


# ============================================================================
# PAGES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
def serve_marketing_landing():
    """Marketing Landing Page: Public-facing marketing page for new visitors."""
    with open("templates/marketing_landing.html", "r") as f:
        return f.read()


@app.get("/about", response_class=HTMLResponse)
def serve_about_page():
    """About Page: Information about HossAgent."""
    with open("templates/about.html", "r") as f:
        return f.read()


@app.get("/how-it-works", response_class=HTMLResponse)
def serve_how_it_works_page():
    """How It Works Page: Guide for using HossAgent."""
    with open("templates/how_it_works.html", "r") as f:
        return f.read()


# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================


@app.get("/signup", response_class=HTMLResponse)
def signup_get():
    """Render signup form."""
    with open("templates/auth_signup.html", "r") as f:
        template = f.read()
    
    html = template.format(
        error_html="",
        company="",
        contact_name="",
        email="",
        niche="",
        geography=""
    )
    return html


@app.post("/signup", response_class=HTMLResponse)
def signup_post(
    request: Request,
    company: str = Form(...),
    contact_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    niche: str = Form(""),
    geography: str = Form(""),
    session: Session = Depends(get_session)
):
    """Process signup form."""
    with open("templates/auth_signup.html", "r") as f:
        template = f.read()
    
    def render_error(error_msg: str) -> str:
        error_html = f'<div class="error-message">{error_msg}</div>'
        return template.format(
            error_html=error_html,
            company=company,
            contact_name=contact_name,
            email=email,
            niche=niche,
            geography=geography
        )
    
    if password != password_confirm:
        return HTMLResponse(content=render_error("Passwords do not match"))
    
    if len(password) < 8:
        return HTMLResponse(content=render_error("Password must be at least 8 characters"))
    
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    
    is_allowed, block_reason = check_trial_abuse(
        session=session,
        email=email.lower().strip(),
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    if not is_allowed:
        return HTMLResponse(content=render_error(f"Unable to create trial: {block_reason}"))
    
    existing = session.exec(
        select(Customer).where(Customer.contact_email == email.lower().strip())
    ).first()
    if existing:
        return HTMLResponse(content=render_error("An account with this email already exists. Please log in."))
    
    customer = Customer(
        company=company.strip(),
        contact_name=contact_name.strip(),
        contact_email=email.lower().strip(),
        password_hash=hash_password(password),
        public_token=generate_public_token(),
        niche=niche.strip() if niche else None,
        geography=geography.strip() if geography else None
    )
    
    customer = initialize_trial(customer)
    
    session.add(customer)
    session.flush()
    
    record_trial_identity(
        session=session,
        customer_id=customer.id,
        email=email.lower().strip(),
        ip_address=ip_address,
        user_agent=user_agent
    )
    
    session.commit()
    
    print(f"[SIGNUP] New customer created: {customer.company} ({customer.contact_email})")
    
    session_token = create_customer_session(customer.id)
    response = RedirectResponse(url="/portal", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax"
    )
    return response


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    """Render login form."""
    with open("templates/auth_login.html", "r") as f:
        template = f.read()
    
    message_html = ""
    if request.query_params.get("registered") == "true":
        message_html = '<div class="success-message">Account created successfully. Please log in.</div>'
    elif request.query_params.get("logout") == "true":
        message_html = '<div class="success-message">You have been logged out.</div>'
    
    html = template.format(
        message_html=message_html,
        email=""
    )
    return html


@app.post("/login", response_class=HTMLResponse)
def login_post(
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    """Process login form."""
    with open("templates/auth_login.html", "r") as f:
        template = f.read()
    
    customer, error = authenticate_customer(session, email, password)
    
    if error:
        error_html = f'<div class="error-message">{error}</div>'
        html = template.format(
            message_html=error_html,
            email=email
        )
        return HTMLResponse(content=html)
    
    session_token = create_customer_session(customer.id)
    response = RedirectResponse(url="/portal", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax"
    )
    
    print(f"[LOGIN] Customer logged in: {customer.contact_email}")
    return response


@app.get("/logout")
def logout():
    """Clear session and redirect to home."""
    response = RedirectResponse(url="/?logout=true", status_code=303)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response


# ============================================================================
# FORGOT PASSWORD ROUTES
# ============================================================================


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_get(request: Request):
    """Render forgot password form."""
    with open("templates/forgot_password.html", "r") as f:
        template = f.read()
    
    message_html = ""
    if request.query_params.get("sent") == "true":
        message_html = '<div class="success-message">If an account with that email exists, a reset link has been sent.</div>'
    
    html = template.format(
        message_html=message_html,
        email=""
    )
    return html


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_post(
    request: Request,
    email: str = Form(...),
    session: Session = Depends(get_session)
):
    """Process forgot password form - generate reset token and send email."""
    customer = session.exec(
        select(Customer).where(Customer.contact_email == email.lower().strip())
    ).first()
    
    if customer:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)
        
        reset_token = PasswordResetToken(
            customer_id=customer.id,
            token=token,
            expires_at=expires_at
        )
        session.add(reset_token)
        session.commit()
        
        host = request.headers.get("host", "localhost:5000")
        scheme = "https" if "https" in request.url.scheme or host.endswith(".repl.co") or host.endswith(".replit.dev") or host.endswith(".replit.app") else "http"
        reset_url = f"{scheme}://{host}/reset-password?token={token}"
        
        reset_email_subject = "Reset Your HossAgent Password"
        reset_email_body = f"""Hello {customer.contact_name or 'there'},

You requested a password reset for your HossAgent account.

Click the link below to reset your password:
{reset_url}

This link will expire in 1 hour.

If you didn't request this reset, you can safely ignore this email.

Best,
The HossAgent Team
"""
        
        send_email(
            to_email=customer.contact_email,
            subject=reset_email_subject,
            body=reset_email_body,
            lead_name=customer.contact_name or "",
            company=customer.company
        )
        
        print(f"[FORGOT_PASSWORD] Reset token generated for: {customer.contact_email}")
    else:
        print(f"[FORGOT_PASSWORD] No account found for: {email}")
    
    return RedirectResponse(url="/forgot-password?sent=true", status_code=303)


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_get(
    token: str = Query(None),
    session: Session = Depends(get_session)
):
    """Render reset password form."""
    with open("templates/reset_password.html", "r") as f:
        template = f.read()
    
    if not token:
        html = template.format(
            message_html='<div class="error-message">Invalid reset link. Please request a new password reset.</div>',
            form_html='<p style="text-align: center; color: #888;">No valid reset token provided.</p>'
        )
        return html
    
    reset_token = session.exec(
        select(PasswordResetToken).where(PasswordResetToken.token == token)
    ).first()
    
    if not reset_token:
        html = template.format(
            message_html='<div class="error-message">Invalid reset link. Please request a new password reset.</div>',
            form_html='<p style="text-align: center; color: #888;"><a href="/forgot-password" style="color: #999;">Request a new reset link</a></p>'
        )
        return html
    
    if reset_token.used:
        html = template.format(
            message_html='<div class="error-message">This reset link has already been used. Please request a new password reset.</div>',
            form_html='<p style="text-align: center; color: #888;"><a href="/forgot-password" style="color: #999;">Request a new reset link</a></p>'
        )
        return html
    
    if datetime.utcnow() > reset_token.expires_at:
        html = template.format(
            message_html='<div class="error-message">This reset link has expired. Please request a new password reset.</div>',
            form_html='<p style="text-align: center; color: #888;"><a href="/forgot-password" style="color: #999;">Request a new reset link</a></p>'
        )
        return html
    
    form_html = f'''
            <form method="POST" action="/reset-password">
                <input type="hidden" name="token" value="{token}">
                
                <div class="form-group">
                    <label>New Password</label>
                    <input type="password" name="password" placeholder="Enter new password" required minlength="8">
                </div>
                
                <div class="form-group">
                    <label>Confirm Password</label>
                    <input type="password" name="password_confirm" placeholder="Confirm new password" required minlength="8">
                </div>
                
                <button type="submit" class="btn-submit">Reset Password</button>
            </form>
    '''
    
    html = template.format(
        message_html="",
        form_html=form_html
    )
    return html


@app.post("/reset-password", response_class=HTMLResponse)
def reset_password_post(
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    session: Session = Depends(get_session)
):
    """Process reset password form - validate token and set new password."""
    with open("templates/reset_password.html", "r") as f:
        template = f.read()
    
    def render_error(error_msg: str, show_form: bool = False) -> str:
        error_html = f'<div class="error-message">{error_msg}</div>'
        if show_form:
            form_html = f'''
            <form method="POST" action="/reset-password">
                <input type="hidden" name="token" value="{token}">
                
                <div class="form-group">
                    <label>New Password</label>
                    <input type="password" name="password" placeholder="Enter new password" required minlength="8">
                </div>
                
                <div class="form-group">
                    <label>Confirm Password</label>
                    <input type="password" name="password_confirm" placeholder="Confirm new password" required minlength="8">
                </div>
                
                <button type="submit" class="btn-submit">Reset Password</button>
            </form>
            '''
        else:
            form_html = '<p style="text-align: center; color: #888;"><a href="/forgot-password" style="color: #999;">Request a new reset link</a></p>'
        return template.format(message_html=error_html, form_html=form_html)
    
    reset_token = session.exec(
        select(PasswordResetToken).where(PasswordResetToken.token == token)
    ).first()
    
    if not reset_token:
        return HTMLResponse(content=render_error("Invalid reset link. Please request a new password reset."))
    
    if reset_token.used:
        return HTMLResponse(content=render_error("This reset link has already been used. Please request a new password reset."))
    
    if datetime.utcnow() > reset_token.expires_at:
        return HTMLResponse(content=render_error("This reset link has expired. Please request a new password reset."))
    
    if password != password_confirm:
        return HTMLResponse(content=render_error("Passwords do not match.", show_form=True))
    
    if len(password) < 8:
        return HTMLResponse(content=render_error("Password must be at least 8 characters.", show_form=True))
    
    customer = session.exec(
        select(Customer).where(Customer.id == reset_token.customer_id)
    ).first()
    
    if not customer:
        return HTMLResponse(content=render_error("Account not found. Please contact support."))
    
    customer.password_hash = hash_password(password)
    reset_token.used = True
    session.add(customer)
    session.add(reset_token)
    session.commit()
    
    print(f"[RESET_PASSWORD] Password reset for: {customer.contact_email}")
    
    session_token = create_customer_session(customer.id)
    response = RedirectResponse(url="/portal", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax"
    )
    
    return response


# ============================================================================
# ADMIN AUTHENTICATION ROUTES
# ============================================================================


@app.get("/admin", response_class=HTMLResponse)
def serve_admin_console(request: Request, session: Session = Depends(get_session)):
    """Admin Console: Operator controls for system management (requires authentication)."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    
    if not verify_admin_session(admin_token):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    with open("templates/admin_console.html", "r") as f:
        return f.read()


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_get():
    """Render admin login form."""
    with open("templates/admin_login.html", "r") as f:
        template = f.read()
    
    html = template.format(error_html="")
    return html


@app.post("/admin/login", response_class=HTMLResponse)
def admin_login_post(
    password: str = Form(...)
):
    """Process admin login form."""
    with open("templates/admin_login.html", "r") as f:
        template = f.read()
    
    admin_password = get_admin_password()
    
    if password != admin_password:
        error_html = '<div class="error-message">Invalid password</div>'
        html = template.format(error_html=error_html)
        return HTMLResponse(content=html)
    
    admin_token = create_admin_session()
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=admin_token,
        max_age=ADMIN_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax"
    )
    
    print("[ADMIN] Admin logged in")
    return response


# ============================================================================
# SESSION-BASED PORTAL ROUTE
# ============================================================================


@app.get("/portal", response_class=HTMLResponse)
def portal_session_based(request: Request, session: Session = Depends(get_session)):
    """
    Session-based customer portal for logged-in customers.
    
    Requires a valid session cookie. Redirects to login if not authenticated.
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return RedirectResponse(url="/login", status_code=303)
    
    return render_customer_portal(customer, request, session)


@app.get("/portal/settings", response_class=HTMLResponse)
def portal_settings_get(request: Request, session: Session = Depends(get_session)):
    """Display business profile / settings form."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return RedirectResponse(url="/login", status_code=303)
    
    profile = session.exec(
        select(BusinessProfile).where(BusinessProfile.customer_id == customer.id)
    ).first()
    
    with open("templates/portal_settings.html", "r") as f:
        template = f.read()
    
    def selected(value, check):
        return 'selected="selected"' if value == check else ''
    
    html = template.format(
        message_html="",
        short_description=profile.short_description or "" if profile else "",
        services=profile.services or "" if profile else "",
        pricing_notes=profile.pricing_notes or "" if profile else "",
        ideal_customer=profile.ideal_customer or "" if profile else "",
        excluded_customers=profile.excluded_customers or "" if profile else "",
        voice_tone_professional=selected(profile.voice_tone if profile else "", "professional"),
        voice_tone_friendly=selected(profile.voice_tone if profile else "", "friendly"),
        voice_tone_casual=selected(profile.voice_tone if profile else "", "casual"),
        voice_tone_formal=selected(profile.voice_tone if profile else "", "formal"),
        voice_tone_confident=selected(profile.voice_tone if profile else "", "confident"),
        comm_style_direct=selected(profile.communication_style if profile else "", "direct"),
        comm_style_conversational=selected(profile.communication_style if profile else "", "conversational"),
        comm_style_storytelling=selected(profile.communication_style if profile else "", "storytelling"),
        comm_style_data=selected(profile.communication_style if profile else "", "data-driven"),
        constraints=profile.constraints or "" if profile else "",
        primary_contact_name=profile.primary_contact_name or customer.contact_name or "" if profile else customer.contact_name or "",
        primary_contact_email=profile.primary_contact_email or customer.contact_email or "" if profile else customer.contact_email or "",
        outreach_mode_auto='selected="selected"' if customer.outreach_mode == "AUTO" else "",
        outreach_mode_review='selected="selected"' if customer.outreach_mode == "REVIEW" else "",
        autopilot_enabled_true='selected="selected"' if customer.autopilot_enabled else "",
        autopilot_enabled_false='selected="selected"' if not customer.autopilot_enabled else "",
        do_not_contact_list=profile.do_not_contact_list or "" if profile else ""
    )
    
    return HTMLResponse(content=html)


@app.post("/portal/settings", response_class=HTMLResponse)
def portal_settings_post(
    request: Request,
    short_description: str = Form(""),
    services: str = Form(""),
    pricing_notes: str = Form(""),
    ideal_customer: str = Form(""),
    excluded_customers: str = Form(""),
    voice_tone: str = Form(""),
    communication_style: str = Form(""),
    constraints: str = Form(""),
    primary_contact_name: str = Form(""),
    primary_contact_email: str = Form(""),
    outreach_mode: str = Form("AUTO"),
    autopilot_enabled: str = Form("true"),
    do_not_contact_list: str = Form(""),
    session: Session = Depends(get_session)
):
    """Save business profile / settings."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return RedirectResponse(url="/login", status_code=303)
    
    profile = session.exec(
        select(BusinessProfile).where(BusinessProfile.customer_id == customer.id)
    ).first()
    
    if not profile:
        profile = BusinessProfile(customer_id=customer.id)
    
    profile.short_description = short_description.strip() or None
    profile.services = services.strip() or None
    profile.pricing_notes = pricing_notes.strip() or None
    profile.ideal_customer = ideal_customer.strip() or None
    profile.excluded_customers = excluded_customers.strip() or None
    profile.voice_tone = voice_tone.strip() or None
    profile.communication_style = communication_style.strip() or None
    profile.constraints = constraints.strip() or None
    profile.primary_contact_name = primary_contact_name.strip() or None
    profile.primary_contact_email = primary_contact_email.strip() or None
    profile.do_not_contact_list = do_not_contact_list.strip() or None
    profile.updated_at = datetime.utcnow()
    
    customer.outreach_mode = outreach_mode if outreach_mode in ["AUTO", "REVIEW"] else "AUTO"
    customer.autopilot_enabled = autopilot_enabled.lower() == "true"
    
    session.add(profile)
    session.add(customer)
    session.commit()
    
    print(f"[PORTAL] Settings saved for customer {customer.id}: {customer.company} (autopilot={'ON' if customer.autopilot_enabled else 'OFF'})")
    
    with open("templates/portal_settings.html", "r") as f:
        template = f.read()
    
    def selected(value, check):
        return 'selected="selected"' if value == check else ''
    
    html = template.format(
        message_html='<div class="success-message">Settings saved successfully!</div>',
        short_description=profile.short_description or "",
        services=profile.services or "",
        pricing_notes=profile.pricing_notes or "",
        ideal_customer=profile.ideal_customer or "",
        excluded_customers=profile.excluded_customers or "",
        voice_tone_professional=selected(profile.voice_tone, "professional"),
        voice_tone_friendly=selected(profile.voice_tone, "friendly"),
        voice_tone_casual=selected(profile.voice_tone, "casual"),
        voice_tone_formal=selected(profile.voice_tone, "formal"),
        voice_tone_confident=selected(profile.voice_tone, "confident"),
        comm_style_direct=selected(profile.communication_style, "direct"),
        comm_style_conversational=selected(profile.communication_style, "conversational"),
        comm_style_storytelling=selected(profile.communication_style, "storytelling"),
        comm_style_data=selected(profile.communication_style, "data-driven"),
        constraints=profile.constraints or "",
        primary_contact_name=profile.primary_contact_name or "",
        primary_contact_email=profile.primary_contact_email or "",
        outreach_mode_auto='selected="selected"' if customer.outreach_mode == "AUTO" else "",
        outreach_mode_review='selected="selected"' if customer.outreach_mode == "REVIEW" else "",
        autopilot_enabled_true='selected="selected"' if customer.autopilot_enabled else "",
        autopilot_enabled_false='selected="selected"' if not customer.autopilot_enabled else "",
        do_not_contact_list=profile.do_not_contact_list or ""
    )
    
    return HTMLResponse(content=html)


@app.post("/portal/cancel")
def portal_cancel_subscription(request: Request, session: Session = Depends(get_session)):
    """
    Cancel subscription at end of billing period.
    
    Only available for paid users with active subscription.
    Sets cancelled_at_period_end = True but keeps subscription_status as "active"
    so they can continue using the service until the billing period ends.
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return RedirectResponse(url="/login", status_code=303)
    
    plan_status = get_customer_plan_status(customer)
    
    if not plan_status.is_paid:
        return RedirectResponse(url="/portal?error=not_subscribed", status_code=303)
    
    customer.cancelled_at_period_end = True
    session.add(customer)
    session.commit()
    
    print(f"[PORTAL] Subscription cancellation scheduled for customer {customer.id}: {customer.company}")
    
    return RedirectResponse(url="/portal?cancelled=true", status_code=303)


@app.post("/portal/reactivate")
def portal_reactivate_subscription(request: Request, session: Session = Depends(get_session)):
    """
    Reactivate a subscription that was scheduled for cancellation.
    
    Sets cancelled_at_period_end = False to resume the subscription.
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return RedirectResponse(url="/login", status_code=303)
    
    plan_status = get_customer_plan_status(customer)
    
    if not plan_status.is_paid:
        return RedirectResponse(url="/portal?error=not_subscribed", status_code=303)
    
    customer.cancelled_at_period_end = False
    customer.cancellation_effective_at = None
    session.add(customer)
    session.commit()
    
    print(f"[PORTAL] Subscription reactivated for customer {customer.id}: {customer.company}")
    
    return RedirectResponse(url="/portal?reactivated=true", status_code=303)


@app.post("/api/outreach/{outreach_id}/{action}")
def handle_outreach_action(
    outreach_id: int,
    action: str,
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Handle pending outreach actions: approve, edit, or skip.
    
    Actions:
    - approve: Send the email immediately
    - edit: Mark for editing (redirect to edit page - for now just approve)
    - skip: Mark as skipped
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return JSONResponse(status_code=401, content={"success": False, "error": "Not authenticated"})
    
    outreach = session.exec(
        select(PendingOutbound).where(
            PendingOutbound.id == outreach_id,
            PendingOutbound.customer_id == customer.id
        )
    ).first()
    
    if not outreach:
        return JSONResponse(status_code=404, content={"success": False, "error": "Outreach not found"})
    
    if outreach.status != "PENDING":
        return JSONResponse(content={"success": False, "error": "Already processed"})
    
    if action == "approve" or action == "edit":
        outreach.status = "APPROVED"
        outreach.approved_at = datetime.utcnow()
        
        email_success = send_email(
            to_email=outreach.to_email,
            subject=outreach.subject,
            body=outreach.body,
            lead_name=outreach.to_name or "",
            company=""
        )
        
        if email_success:
            outreach.status = "SENT"
            outreach.sent_at = datetime.utcnow()
            print(f"[OUTREACH] Email sent: {outreach.id} to {outreach.to_email}")
        else:
            print(f"[OUTREACH] Email queued (dry-run): {outreach.id}")
        
        session.add(outreach)
        session.commit()
        return JSONResponse(content={"success": True, "action": "sent" if email_success else "approved"})
    
    elif action == "skip":
        outreach.status = "SKIPPED"
        outreach.skipped_reason = "Skipped by customer"
        session.add(outreach)
        session.commit()
        print(f"[OUTREACH] Skipped: {outreach.id}")
        return JSONResponse(content={"success": True, "action": "skipped"})
    
    return JSONResponse(status_code=400, content={"success": False, "error": "Invalid action"})


@app.post("/api/upgrade")
def admin_upgrade_customer(
    customer_id: int = Query(..., description="Customer ID to upgrade"),
    request: Request = None,
    session: Session = Depends(get_session)
):
    """
    Admin endpoint to upgrade a customer to paid plan via admin override.
    
    Sets:
    - customer.plan = "paid"
    - customer.subscription_status = "active"
    - customer.billing_method = "ADMIN_OVERRIDE"
    - Clears trial_end_at
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME) if request else None
    if not verify_admin_session(admin_token):
        return JSONResponse(status_code=403, content={"success": False, "error": "Admin access required"})
    
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    
    if not customer:
        return JSONResponse(status_code=404, content={"success": False, "error": "Customer not found"})
    
    customer.plan = "paid"
    customer.subscription_status = "active"
    customer.billing_method = "ADMIN_OVERRIDE"
    customer.trial_end_at = None
    
    session.add(customer)
    session.commit()
    
    print(f"[ADMIN] Customer {customer.id} ({customer.company}) upgraded to PAID via admin override")
    
    return JSONResponse(content={
        "success": True,
        "customer_id": customer.id,
        "company": customer.company,
        "plan": customer.plan,
        "subscription_status": customer.subscription_status,
        "billing_method": customer.billing_method
    })


@app.get("/api/pending-outreach")
def get_all_pending_outreach(
    request: Request,
    limit: int = Query(default=50, le=100),
    session: Session = Depends(get_session)
):
    """
    Get all pending outreach records across all customers (admin only).
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    outreach_records = session.exec(
        select(PendingOutbound).order_by(PendingOutbound.created_at.desc()).limit(limit)
    ).all()
    
    result = []
    for po in outreach_records:
        customer = session.exec(
            select(Customer).where(Customer.id == po.customer_id)
        ).first()
        
        result.append({
            "id": po.id,
            "customer_id": po.customer_id,
            "customer_company": customer.company if customer else "Unknown",
            "to_email": po.to_email,
            "subject": po.subject,
            "status": po.status,
            "created_at": po.created_at.isoformat() if po.created_at else None
        })
    
    return result


@app.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_detail(customer_id: int, session: Session = Depends(get_session)):
    """Customer detail page."""
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    leads = session.exec(
        select(Lead).where(Lead.email == customer.contact_email)
    ).all()
    tasks = session.exec(
        select(Task).where(Task.customer_id == customer_id)
    ).all()
    invoices = session.exec(
        select(Invoice).where(Invoice.customer_id == customer_id)
    ).all()

    html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>{customer.company} - HossAgent</title>
    <style>body{{background:#0a0a0a;color:#fff;font-family:Georgia,serif;padding:2rem}}</style>
    </head><body>
    <a href="/">← Back to Dashboard</a>
    <h1>{customer.company}</h1>
    <p><strong>Email:</strong> {customer.contact_email}</p>
    <p><strong>Plan:</strong> {customer.billing_plan}</p>
    <p><strong>Status:</strong> {customer.status}</p>
    <h2>Tasks ({len(tasks)})</h2>
    <ul>
    {''.join(f"<li>Task {t.id}: {t.description} ({t.status}) - ${t.profit_cents/100:.2f}</li>" for t in tasks)}
    </ul>
    <h2>Invoices ({len(invoices)})</h2>
    <ul>
    {''.join(f"<li>Invoice {i.id}: ${i.amount_cents/100:.2f} ({i.status})</li>" for i in invoices)}
    </ul>
    </body></html>
    """
    return html


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(lead_id: int, session: Session = Depends(get_session)):
    """Lead detail page."""
    lead = session.exec(select(Lead).where(Lead.id == lead_id)).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    customer = session.exec(
        select(Customer).where(Customer.contact_email == lead.email)
    ).first()
    tasks = []
    if customer:
        tasks = session.exec(
            select(Task).where(Task.customer_id == customer.id)
        ).all()

    html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>{lead.company} - Lead</title>
    <style>body{{background:#0a0a0a;color:#fff;font-family:Georgia,serif;padding:2rem}}</style>
    </head><body>
    <a href="/">← Back to Dashboard</a>
    <h1>{lead.company}</h1>
    <p><strong>Contact:</strong> {lead.name} ({lead.email})</p>
    <p><strong>Niche:</strong> {lead.niche}</p>
    <p><strong>Status:</strong> {lead.status}</p>
    <p><strong>Last Contacted:</strong> {lead.last_contacted_at.strftime("%Y-%m-%d %H:%M") if lead.last_contacted_at else "Never"}</p>
    {'<h2>Customer</h2><p>Company: ' + customer.company + ' (ID: ' + str(customer.id) + ')</p>' if customer else '<p><em>Not yet converted to customer.</em></p>'}
    <h2>Tasks</h2>
    <ul>
    {''.join(f"<li>Task {t.id}: {t.description} ({t.status})</li>" for t in tasks) or '<li><em>None</em></li>'}
    </ul>
    </body></html>
    """
    return html


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(invoice_id: int, session: Session = Depends(get_session)):
    """Invoice detail page."""
    invoice = session.exec(
        select(Invoice).where(Invoice.id == invoice_id)
    ).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    customer = session.exec(
        select(Customer).where(Customer.id == invoice.customer_id)
    ).first()
    tasks = session.exec(
        select(Task).where(Task.customer_id == invoice.customer_id)
    ).all()

    html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>Invoice {invoice.id}</title>
    <style>body{{background:#0a0a0a;color:#fff;font-family:Georgia,serif;padding:2rem}}</style>
    </head><body>
    <a href="/">← Back to Dashboard</a>
    <h1>Invoice {invoice.id}</h1>
    <p><strong>Customer:</strong> {customer.company if customer else 'Unknown'}</p>
    <p><strong>Amount:</strong> ${invoice.amount_cents/100:.2f}</p>
    <p><strong>Status:</strong> {invoice.status}</p>
    <p><strong>Created:</strong> {invoice.created_at.strftime("%Y-%m-%d %H:%M")}</p>
    <p><strong>Paid:</strong> {invoice.paid_at.strftime("%Y-%m-%d %H:%M") if invoice.paid_at else "—"}</p>
    <p><strong>Notes:</strong> {invoice.notes or 'None'}</p>
    <h2>Related Tasks</h2>
    <ul>
    {''.join(f"<li>Task {t.id}: {t.description} - ${t.profit_cents/100:.2f}</li>" for t in tasks) or '<li><em>None</em></li>'}
    </ul>
    </body></html>
    """
    return html


# ============================================================================
# API ENDPOINTS - DATA RETRIEVAL
# ============================================================================


@app.get("/api/leads")
def get_leads(session: Session = Depends(get_session)):
    """Get all leads."""
    leads = session.exec(select(Lead)).all()
    return [
        {
            "id": l.id,
            "name": l.name,
            "email": l.email,
            "company": l.company,
            "niche": l.niche,
            "status": l.status,
            "website": l.website,
            "source": l.source,
            "last_contacted_at": l.last_contacted_at.isoformat()
            if l.last_contacted_at
            else None,
            "created_at": l.created_at.isoformat(),
        }
        for l in leads
    ]


@app.get("/api/customers")
def get_customers(session: Session = Depends(get_session)):
    """Get all customers."""
    customers = session.exec(select(Customer)).all()
    return [
        {
            "id": c.id,
            "company": c.company,
            "contact_email": c.contact_email,
            "plan": c.plan,
            "billing_plan": c.billing_plan,
            "status": c.status,
            "stripe_customer_id": c.stripe_customer_id,
            "public_token": c.public_token,
            "notes": c.notes,
            "created_at": c.created_at.isoformat(),
        }
        for c in customers
    ]


@app.get("/api/tasks")
def get_tasks(session: Session = Depends(get_session)):
    """Get all tasks."""
    tasks = session.exec(select(Task)).all()
    return [
        {
            "id": t.id,
            "customer_id": t.customer_id,
            "description": t.description,
            "status": t.status,
            "reward_cents": t.reward_cents,
            "cost_cents": t.cost_cents,
            "profit_cents": t.profit_cents,
            "result_summary": t.result_summary,
            "created_at": t.created_at.isoformat(),
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in tasks
    ]


@app.get("/api/invoices")
def get_invoices(session: Session = Depends(get_session)):
    """Get all invoices."""
    invoices = session.exec(select(Invoice)).all()
    return [
        {
            "id": i.id,
            "customer_id": i.customer_id,
            "amount_cents": i.amount_cents,
            "status": i.status,
            "created_at": i.created_at.isoformat(),
            "paid_at": i.paid_at.isoformat() if i.paid_at else None,
            "notes": i.notes,
        }
        for i in invoices
    ]


# ============================================================================
# API ENDPOINTS - ADMIN CONTROLS
# ============================================================================


@app.post("/admin/autopilot")
async def toggle_autopilot(enabled: bool, session: Session = Depends(get_session)):
    """Toggle autopilot mode on/off."""
    settings = session.exec(
        select(SystemSettings).where(SystemSettings.id == 1)
    ).first()
    if settings:
        settings.autopilot_enabled = enabled
        session.add(settings)
        session.commit()
        status = "enabled" if enabled else "disabled"
        print(f"[ADMIN] Autopilot {status}")
        return {"status": f"Autopilot {status}"}
    return {"error": "SystemSettings not found"}


@app.get("/api/settings")
def get_settings(session: Session = Depends(get_session)):
    """Get current system settings including email and release mode configuration."""
    settings = session.exec(
        select(SystemSettings).where(SystemSettings.id == 1)
    ).first()
    email_status = get_email_status()
    release_status = get_release_mode_status()
    
    if settings:
        return {
            "autopilot_enabled": settings.autopilot_enabled,
            "email": email_status,
            "release_mode": release_status
        }
    return {"error": "Settings not found", "email": email_status, "release_mode": release_status}


@app.get("/api/email-log")
def get_email_log_endpoint(limit: int = Query(default=10, le=50)):
    """Get recent email attempts for admin console display."""
    return {"entries": get_email_log(limit)}


@app.get("/api/lead-source")
def get_lead_source_endpoint():
    """
    Get current lead source configuration and status.
    
    Returns:
        - niche: Target ICP description
        - geography: Geographic constraint (if any)
        - provider: Current provider (DummySeed or SearchApi)
        - max_new_leads_per_cycle: Lead generation cap
        - last_run: Timestamp of last lead generation run
        - last_created_count: Number of leads created in last run
    """
    status = get_lead_source_status()
    log = get_lead_source_log()
    
    return {
        **status,
        "last_run": log.get("last_run"),
        "last_created_count": log.get("last_created_count", 0),
        "runs": log.get("runs", [])[-10:],
        "recent_leads": log.get("recent_leads", [])[-10:]
    }


@app.post("/api/run/lead-source")
def run_lead_source_manual(session: Session = Depends(get_session)):
    """Manually trigger lead source generation cycle."""
    message = generate_new_leads_from_source(session)
    return {"message": message}


# ============================================================================
# API ENDPOINTS - APOLLO.IO INTEGRATION
# ============================================================================


@app.get("/api/apollo/status")
def get_apollo_status_endpoint(request: Request):
    """Get Apollo.io connection status and usage stats."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        from apollo_integration import get_apollo_status
        return get_apollo_status()
    except ImportError:
        return {"connected": False, "error": "Apollo module not available"}


@app.post("/api/apollo/connect")
def connect_apollo_endpoint(request: Request, api_key: str = Query(...)):
    """
    Connect Apollo.io with API key.
    Validates key before saving.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        from apollo_integration import connect_apollo_with_key
        result = connect_apollo_with_key(api_key)
        return result
    except ImportError:
        return {"success": False, "error": "Apollo module not available"}


@app.post("/api/apollo/disconnect")
def disconnect_apollo_endpoint(request: Request):
    """Disconnect Apollo.io integration."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        from apollo_integration import disconnect_apollo
        return disconnect_apollo()
    except ImportError:
        return {"success": False, "error": "Apollo module not available"}


@app.post("/api/apollo/test")
def test_apollo_endpoint(request: Request):
    """
    Test Apollo connection by fetching sample leads.
    Returns first 5 leads from Miami HVAC search.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        from apollo_integration import test_apollo_connection
        return test_apollo_connection()
    except ImportError:
        return {"success": False, "error": "Apollo module not available"}


@app.get("/api/apollo/log")
def get_apollo_log_endpoint(request: Request, limit: int = Query(default=20, le=100)):
    """Get Apollo.io fetch log entries."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        from apollo_integration import get_fetch_log
        return {"entries": get_fetch_log(limit)}
    except ImportError:
        return {"entries": [], "error": "Apollo module not available"}


@app.post("/api/lead-source/preference")
def set_lead_source_preference_endpoint(request: Request, source: str = Query(...)):
    """
    Set lead source preference.
    
    Args:
        source: "apollo", "dummy", or "auto"
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from lead_sources import set_lead_source_preference, get_lead_source_preference
    
    if set_lead_source_preference(source):
        return {"success": True, "preference": get_lead_source_preference()}
    else:
        return {"success": False, "error": f"Invalid source: {source}. Must be 'apollo', 'dummy', or 'auto'"}


# ============================================================================
# API ENDPOINTS - AGENT EXECUTION (MANUAL TRIGGERS)
# ============================================================================


@app.post("/api/run/bizdev")
async def run_bizdev(session: Session = Depends(get_session)):
    """Manually trigger BizDev cycle."""
    message = await run_bizdev_cycle(session)
    return {"message": message}


@app.post("/api/run/onboarding")
async def run_onboarding(session: Session = Depends(get_session)):
    """Manually trigger Onboarding cycle."""
    message = await run_onboarding_cycle(session)
    return {"message": message}


@app.post("/api/run/ops")
async def run_ops(session: Session = Depends(get_session)):
    """Manually trigger Ops cycle."""
    message = await run_ops_cycle(session)
    return {"message": message}


@app.post("/api/run/billing")
async def run_billing(session: Session = Depends(get_session)):
    """Manually trigger Billing cycle."""
    message = await run_billing_cycle(session)
    return {"message": message}


@app.post("/api/run/signals")
def run_signals_manual(request: Request, session: Session = Depends(get_session)):
    """
    Manually trigger Signals Agent cycle (admin only).
    
    The Signals Agent:
    - Monitors external context signals about companies
    - Generates LeadEvents for moment-aware outreach
    - Miami-tuned heuristics for South Florida market
    
    Returns:
        - signals_created: Number of new signals generated
        - events_created: Number of new lead events created
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = run_signals_agent(session)
    return {
        "message": f"Signals: Created {result['signals_created']} signals, {result['events_created']} events",
        **result
    }


@app.post("/api/run/event-bizdev")
async def run_event_bizdev(request: Request, session: Session = Depends(get_session)):
    """
    Manually trigger Event-Driven BizDev cycle (admin only).
    
    Processes LeadEvents with status='new' and sends contextual Miami-style outreach.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    message = await run_event_driven_bizdev_cycle(session)
    return {"message": message}


@app.get("/api/signals")
def get_signals_endpoint(
    request: Request,
    limit: int = Query(default=20, le=100),
    session: Session = Depends(get_session)
):
    """
    Get all signals (admin only).
    
    Returns recent signals from the Signals Engine ordered by creation date.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    signals = get_signals_summary(session, limit)
    return [
        {
            "id": s.id,
            "company_id": s.company_id,
            "lead_id": s.lead_id,
            "source_type": s.source_type,
            "context_summary": s.context_summary,
            "geography": s.geography,
            "created_at": s.created_at.isoformat() if s.created_at else None
        }
        for s in signals
    ]


@app.get("/api/lead_events")
def get_lead_events_endpoint(
    request: Request,
    limit: int = Query(default=20, le=100),
    session: Session = Depends(get_session)
):
    """
    Get all lead events (admin only).
    
    Returns recent lead events ordered by creation date.
    Includes urgency_score, category, and status for filtering.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    events = get_lead_events_summary(session, limit)
    return [
        {
            "id": e.id,
            "company_id": e.company_id,
            "lead_id": e.lead_id,
            "signal_id": e.signal_id,
            "summary": e.summary,
            "category": e.category,
            "urgency_score": e.urgency_score,
            "status": e.status,
            "recommended_action": e.recommended_action,
            "outbound_message": e.outbound_message,
            "created_at": e.created_at.isoformat() if e.created_at else None
        }
        for e in events
    ]


@app.post("/api/invoices/{invoice_id}/mark-paid")
def mark_invoice_paid(invoice_id: int, session: Session = Depends(get_session)):
    """Mark an invoice as paid (for testing)."""
    invoice = session.exec(
        select(Invoice).where(Invoice.id == invoice_id)
    ).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    invoice.status = "paid"
    invoice.paid_at = datetime.utcnow()
    session.add(invoice)
    session.commit()
    print(f"[ADMIN] Invoice {invoice_id} marked as paid")
    return {"status": "paid", "invoice_id": invoice_id}


@app.post("/admin/send-test-email")
def send_test_email(
    to_email: str = Query(..., description="Recipient email address"),
    subject: Optional[str] = Query(default="HossAgent Test Email", description="Email subject"),
    body: Optional[str] = Query(default=None, description="Email body")
):
    """
    Send a test email to verify configuration.
    
    Usage: POST /admin/send-test-email?to_email=your@email.com
    
    Returns JSON with:
        - mode: Current email mode (DRY_RUN, SENDGRID, SMTP)
        - to: Recipient address
        - success: Whether email was actually sent
        - message: Human-readable status
    """
    email_status = get_email_status()
    mode = email_status["mode"]
    
    if body is None:
        body = f"""This is a test email from HossAgent.

Your outbound email system is configured and working.

Mode: {mode}
Sent at: {datetime.utcnow().isoformat()}

- HossAgent"""
    
    success = send_email(
        to_email=to_email,
        subject=subject or "HossAgent Test Email",
        body=body,
        lead_name="Test",
        company="Test Email"
    )
    
    return {
        "success": success,
        "mode": mode,
        "to": to_email,
        "message": f"Email {'sent successfully' if success else 'logged (dry-run mode)'} via {mode}"
    }


@app.post("/admin/production-cleanup")
def admin_production_cleanup(
    request: Request,
    owner_email_domain: str = Query(default="", description="Domain to identify real customers (e.g., 'gmail.com')"),
    purge_all_signals: bool = Query(default=True, description="If true, delete ALL signals and lead_events (fresh start)"),
    confirm: bool = Query(default=False, description="Set to true to actually run cleanup"),
    session: Session = Depends(get_session)
):
    """
    One-time production database cleanup.
    
    IMPORTANT: This should only be run ONCE during production initialization.
    It removes all dev/test/demo data while preserving real production customers.
    
    Safety:
    - Requires confirm=true to actually run
    - Creates a flag file to prevent re-running
    - Logs all deletions for auditability
    - Produces an audit log file for compliance
    
    Usage:
        POST /admin/production-cleanup?owner_email_domain=gmail.com&purge_all_signals=true&confirm=true
    
    What it deletes:
    - ALL signals and lead_events (if purge_all_signals=true)
    - Signals/events from non-real customers
    - Pending outbound from non-real customers
    - Reports from non-real customers
    - Tasks from non-real customers
    - Invoices with $0 amounts or from non-real customers
    - Leads with fake emails (Lead_*, @example, @test, etc.)
    - Customers with demo company names
    
    What it preserves:
    - Customers matching owner_email_domain
    - Customers with ADMIN in notes
    - Customers with Stripe integration
    - Paid customers with active subscriptions
    
    Returns JSON summary of cleanup actions taken.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token or ""):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if not confirm:
        cleanup_flag = Path("production_cleanup_completed.flag")
        already_run = cleanup_flag.exists()
        
        return {
            "success": False,
            "message": "Dry run - set confirm=true to actually run cleanup",
            "already_run": already_run,
            "warning": "This will permanently delete dev/test data. Make sure you have a backup.",
            "instructions": {
                "owner_email_domain": "Set this to your email domain to identify real customers",
                "purge_all_signals": "Set to true to delete ALL signals/lead_events (recommended for fresh start)",
                "confirm": "Set to true to execute the cleanup"
            }
        }
    
    results = run_production_cleanup(session, owner_email_domain, purge_all_signals)
    
    return {
        "success": True,
        "message": "Production cleanup completed" if not results["already_run"] else "Cleanup already run previously",
        **results
    }


@app.get("/admin/production-status")
def admin_production_status(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Get production readiness status.
    
    Returns current configuration and readiness for production operation.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token or ""):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    email_status = get_email_status()
    cleanup_flag = Path("production_cleanup_completed.flag")
    
    settings = session.exec(select(SystemSettings).where(SystemSettings.id == 1)).first()
    
    customers = session.exec(select(Customer)).all()
    paid_customers = [c for c in customers if c.plan == "paid"]
    trial_customers = [c for c in customers if c.plan == "trial"]
    
    return {
        "email": {
            "mode": email_status["mode"],
            "configured_mode": email_status["configured_mode"],
            "is_valid": email_status["is_valid"],
            "message": email_status["message"],
            "hourly_status": email_status["hourly"]
        },
        "autopilot": {
            "global_enabled": settings.autopilot_enabled if settings else False,
            "cycle_interval": "15 minutes"
        },
        "cleanup": {
            "completed": cleanup_flag.exists(),
            "flag_file": str(cleanup_flag)
        },
        "customers": {
            "total": len(customers),
            "paid": len(paid_customers),
            "trial": len(trial_customers)
        },
        "production_ready": (
            email_status["is_valid"] and 
            email_status["mode"] != "DRY_RUN" and
            cleanup_flag.exists()
        ),
        "recommendations": []
    }


@app.post("/admin/regenerate-payment-links")
def admin_regenerate_payment_links(
    max_invoices: int = Query(default=100, description="Maximum invoices to process"),
    session: Session = Depends(get_session)
):
    """
    Regenerate Stripe payment links for all unpaid invoices missing them.
    
    Use this endpoint to:
    - Fix invoices created before Stripe was enabled
    - Regenerate links after Stripe configuration changes
    - Bulk update invoices missing payment links
    
    Returns JSON summary:
        - invoices_processed: Number of invoices checked
        - links_created: Number of new payment links generated
        - links_failed: Number of link creation failures
        - invoices_skipped: Number of invoices skipped (already have links or are paid)
        - details: Per-invoice breakdown
    """
    from stripe_utils import ensure_invoice_payment_url, is_stripe_enabled, validate_stripe_config
    
    is_valid, config_msg = validate_stripe_config()
    if not is_valid:
        return {
            "success": False,
            "error": config_msg,
            "invoices_processed": 0,
            "links_created": 0,
            "links_failed": 0,
            "invoices_skipped": 0,
            "details": []
        }
    
    invoices = session.exec(
        select(Invoice).where(
            Invoice.status.in_(["draft", "sent"])
        ).limit(max_invoices)
    ).all()
    
    results = {
        "success": True,
        "invoices_processed": len(invoices),
        "links_created": 0,
        "links_failed": 0,
        "invoices_skipped": 0,
        "details": []
    }
    
    for invoice in invoices:
        if invoice.payment_url and len(invoice.payment_url) > 10:
            results["invoices_skipped"] += 1
            results["details"].append({
                "invoice_id": invoice.id,
                "status": "skipped",
                "reason": "Already has payment link"
            })
            continue
        
        customer = session.exec(
            select(Customer).where(Customer.id == invoice.customer_id)
        ).first()
        
        if not customer:
            results["invoices_skipped"] += 1
            results["details"].append({
                "invoice_id": invoice.id,
                "status": "skipped",
                "reason": "No customer found"
            })
            continue
        
        try:
            result = ensure_invoice_payment_url(
                invoice_id=invoice.id,
                amount_cents=invoice.amount_cents,
                customer_id=customer.id,
                customer_email=customer.contact_email,
                customer_company=customer.company,
                invoice_status=invoice.status,
                existing_payment_url=invoice.payment_url
            )
            
            if result.success and result.payment_url:
                invoice.payment_url = result.payment_url
                stripe_id = getattr(result, 'stripe_id', None)
                if stripe_id:
                    invoice.stripe_payment_id = stripe_id
                session.add(invoice)
                results["links_created"] += 1
                url_preview = result.payment_url[:50] + "..." if len(result.payment_url) > 50 else result.payment_url
                results["details"].append({
                    "invoice_id": invoice.id,
                    "status": "created",
                    "payment_url": url_preview
                })
            elif result.mode == "dry_run":
                results["invoices_skipped"] += 1
                results["details"].append({
                    "invoice_id": invoice.id,
                    "status": "skipped",
                    "reason": f"DRY_RUN: {result.error or 'Stripe not available'}"
                })
            else:
                results["links_failed"] += 1
                results["details"].append({
                    "invoice_id": invoice.id,
                    "status": "failed",
                    "error": result.error or "Unknown error"
                })
        except Exception as e:
            results["links_failed"] += 1
            results["details"].append({
                "invoice_id": invoice.id,
                "status": "failed",
                "error": str(e)
            })
            print(f"[ADMIN][REGENERATE][ERROR] Invoice {invoice.id}: {e}")
    
    session.commit()
    
    print(f"[ADMIN][REGENERATE] Processed {results['invoices_processed']} invoices: "
          f"{results['links_created']} created, {results['links_failed']} failed, "
          f"{results['invoices_skipped']} skipped")
    
    return results


# ============================================================================
# STRIPE WEBHOOK
# ============================================================================


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, session: Session = Depends(get_session)):
    """
    Handle Stripe webhook events.
    
    Validates webhook signature and processes payment events.
    Updates invoice status when payment is completed.
    
    Supported events:
      - checkout.session.completed: Payment link checkout completed
      - payment_intent.succeeded: Direct payment succeeded
      - invoice.paid: Stripe invoice marked paid (if using Stripe invoices)
    
    Returns:
      200 with status on success
      400 on invalid signature
      500 on processing error (but doesn't crash app)
    """
    from stripe_utils import verify_webhook_signature, log_stripe_event, get_stripe_webhook_secret
    
    try:
        payload = await request.body()
    except Exception as e:
        print(f"[STRIPE][WEBHOOK] Failed to read request body: {e}")
        raise HTTPException(status_code=400, detail="Invalid request body")
    
    signature = request.headers.get("Stripe-Signature", "")
    
    if not signature:
        print("[STRIPE][WEBHOOK] Missing Stripe-Signature header")
        log_stripe_event("webhook_missing_signature", {})
        raise HTTPException(status_code=400, detail="Missing signature header")
    
    webhook_secret = get_stripe_webhook_secret()
    if not webhook_secret:
        print("[STRIPE][WEBHOOK] No webhook secret configured - accepting event without verification")
        log_stripe_event("webhook_received_no_secret", {"warning": "Unverified - no secret configured"})
    elif not verify_webhook_signature(payload, signature):
        print("[STRIPE][WEBHOOK] Invalid signature - rejecting event")
        log_stripe_event("webhook_invalid_signature", {})
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    import json
    try:
        event = json.loads(payload)
        event_type = event.get("type", "unknown")
        event_id = event.get("id", "unknown")
        event_data = event.get("data", {}).get("object", {})
        
        print(f"[STRIPE][WEBHOOK] Received event: {event_type} (id={event_id})")
        log_stripe_event(f"webhook_{event_type}", {
            "event_id": event_id,
            "type": event_type
        })
        
        invoice_updated = False
        invoice_id = None
        
        if event_type in ["checkout.session.completed", "payment_intent.succeeded", "invoice.paid"]:
            metadata = event_data.get("metadata", {})
            invoice_id = metadata.get("invoice_id")
            
            if not invoice_id and event_type == "checkout.session.completed":
                invoice_id = event_data.get("client_reference_id")
            
            stripe_amount = event_data.get("amount_total") or event_data.get("amount") or event_data.get("amount_paid")
            stripe_currency = (event_data.get("currency") or "").lower()
            stripe_status = event_data.get("status") or event_data.get("payment_status")
            
            from stripe_utils import get_default_currency
            expected_currency = get_default_currency()
            
            payment_successful = False
            if event_type == "checkout.session.completed":
                payment_successful = stripe_status in ["complete", "paid"] or event_data.get("payment_status") == "paid"
            elif event_type == "payment_intent.succeeded":
                payment_successful = stripe_status == "succeeded" or event_type == "payment_intent.succeeded"
            elif event_type == "invoice.paid":
                payment_successful = stripe_status == "paid" or event_data.get("paid") == True
            
            if not invoice_id:
                print(f"[STRIPE][WEBHOOK] No invoice_id in event metadata - cannot process")
                log_stripe_event("webhook_missing_invoice_id", {"event_type": event_type})
            elif not payment_successful:
                print(f"[STRIPE][WEBHOOK] Payment not confirmed (status={stripe_status}) - not marking as paid")
                log_stripe_event("webhook_payment_not_confirmed", {
                    "invoice_id": invoice_id,
                    "status": stripe_status,
                    "event_type": event_type
                })
            else:
                try:
                    invoice = session.exec(
                        select(Invoice).where(Invoice.id == int(invoice_id))
                    ).first()
                    
                    if not invoice:
                        print(f"[STRIPE][WEBHOOK] Invoice {invoice_id} not found in database")
                        log_stripe_event("webhook_invoice_not_found", {"invoice_id": invoice_id})
                    elif invoice.status == "paid":
                        print(f"[STRIPE][WEBHOOK] Invoice {invoice_id} already paid - no action")
                    elif stripe_amount is not None and stripe_amount != invoice.amount_cents:
                        print(f"[STRIPE][WEBHOOK][SECURITY] Amount mismatch for invoice {invoice_id}: expected {invoice.amount_cents}, got {stripe_amount}")
                        log_stripe_event("webhook_amount_mismatch", {
                            "invoice_id": invoice_id,
                            "expected_amount": invoice.amount_cents,
                            "received_amount": stripe_amount
                        })
                    elif stripe_currency and stripe_currency != expected_currency:
                        print(f"[STRIPE][WEBHOOK][SECURITY] Currency mismatch for invoice {invoice_id}: expected {expected_currency}, got {stripe_currency}")
                        log_stripe_event("webhook_currency_mismatch", {
                            "invoice_id": invoice_id,
                            "expected_currency": expected_currency,
                            "received_currency": stripe_currency
                        })
                    else:
                        invoice.status = "paid"
                        invoice.paid_at = datetime.utcnow()
                        session.add(invoice)
                        session.commit()
                        invoice_updated = True
                        print(f"[STRIPE][WEBHOOK] Invoice {invoice_id} marked as PAID (amount=${invoice.amount_cents/100:.2f}, currency={stripe_currency or expected_currency})")
                        log_stripe_event("invoice_paid", {
                            "invoice_id": invoice_id,
                            "amount_cents": invoice.amount_cents,
                            "stripe_amount": stripe_amount,
                            "stripe_currency": stripe_currency,
                            "event_type": event_type
                        })
                except ValueError:
                    print(f"[STRIPE][WEBHOOK] Invalid invoice_id format: {invoice_id}")
        
        return {
            "status": "processed",
            "event_type": event_type,
            "event_id": event_id,
            "invoice_updated": invoice_updated,
            "invoice_id": invoice_id
        }
        
    except json.JSONDecodeError as e:
        print(f"[STRIPE][WEBHOOK] Invalid JSON payload: {e}")
        log_stripe_event("webhook_invalid_json", {"error": str(e)})
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        print(f"[STRIPE][WEBHOOK] Error processing event: {e}")
        log_stripe_event("webhook_error", {"error": str(e)})
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": "Processing failed but acknowledged"}
        )


# ============================================================================
# STRIPE STATUS API
# ============================================================================


@app.get("/api/stripe/status")
def get_stripe_status_endpoint(session: Session = Depends(get_session)):
    """Get current Stripe configuration status including payment link stats."""
    from stripe_utils import get_stripe_status, get_stripe_log
    
    status = get_stripe_status()
    recent_events = get_stripe_log(10)
    
    all_invoices = list(session.exec(select(Invoice)).all())
    invoice_stats = get_invoice_payment_stats(all_invoices)
    
    return {
        **status,
        **invoice_stats,
        "recent_events": recent_events
    }


# ============================================================================
# SUBSCRIPTION MANAGEMENT
# ============================================================================


@app.get("/api/subscription/status")
def get_subscription_status_endpoint():
    """Get current subscription configuration status."""
    return get_subscription_status()


@app.get("/api/customer/{customer_id}/plan")
def get_customer_plan_endpoint(customer_id: int, session: Session = Depends(get_session)):
    """Get plan status for a specific customer."""
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    plan_status = get_customer_plan_status(customer)
    
    return {
        "customer_id": customer_id,
        "company": customer.company,
        "plan": plan_status.plan,
        "is_trial": plan_status.is_trial,
        "is_paid": plan_status.is_paid,
        "is_expired": plan_status.is_expired,
        "days_remaining": plan_status.days_remaining,
        "tasks_used": plan_status.tasks_used,
        "tasks_limit": plan_status.tasks_limit,
        "leads_used": plan_status.leads_used,
        "leads_limit": plan_status.leads_limit,
        "can_run_tasks": plan_status.can_run_tasks,
        "can_generate_leads": plan_status.can_generate_leads,
        "can_send_real_email": plan_status.can_send_real_email,
        "can_use_billing": plan_status.can_use_billing,
        "can_use_autopilot": plan_status.can_use_autopilot,
        "upgrade_required": plan_status.upgrade_required,
        "status_message": plan_status.status_message
    }


@app.post("/upgrade")
def upgrade_customer(
    customer_id: int = Query(..., description="Customer ID to upgrade"),
    session: Session = Depends(get_session)
):
    """
    Upgrade a customer from trial to paid plan.
    
    Creates Stripe customer and subscription, then updates customer plan.
    
    Returns:
        Success/failure with subscription details
    """
    customer = session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    plan_status = get_customer_plan_status(customer)
    
    if plan_status.is_paid:
        return {
            "success": True,
            "message": "Customer already on paid plan",
            "customer_id": customer_id,
            "plan": "paid"
        }
    
    if not is_stripe_enabled():
        customer = upgrade_to_paid(customer, stripe_subscription_id=None)
        session.add(customer)
        session.commit()
        
        print(f"[UPGRADE] Customer {customer_id} upgraded to paid (Stripe disabled)")
        return {
            "success": True,
            "message": "Upgraded to paid plan (Stripe disabled - manual billing)",
            "customer_id": customer_id,
            "plan": "paid",
            "stripe_subscription_id": None
        }
    
    stripe_customer_id = customer.stripe_customer_id
    if not stripe_customer_id:
        stripe_customer_id, error = create_stripe_customer(
            customer_id=customer.id,
            email=customer.contact_email,
            company=customer.company
        )
        
        if error:
            print(f"[UPGRADE][ERROR] Failed to create Stripe customer: {error}")
            return {
                "success": False,
                "error": f"Failed to create Stripe customer: {error}",
                "customer_id": customer_id
            }
        
        customer.stripe_customer_id = stripe_customer_id
        session.add(customer)
        session.flush()
    
    if not stripe_customer_id:
        return {
            "success": False,
            "error": "Stripe customer ID missing",
            "customer_id": customer_id
        }
    
    subscription_id, error = create_subscription(
        stripe_customer_id=stripe_customer_id,
        customer_id=customer.id
    )
    
    if error:
        print(f"[UPGRADE][ERROR] Failed to create subscription: {error}")
        return {
            "success": False,
            "error": f"Failed to create subscription: {error}",
            "customer_id": customer_id,
            "stripe_customer_id": stripe_customer_id
        }
    
    if not subscription_id:
        return {
            "success": False,
            "error": "Failed to get subscription ID",
            "customer_id": customer_id,
            "stripe_customer_id": stripe_customer_id
        }
    
    customer = upgrade_to_paid(customer, stripe_subscription_id=subscription_id)
    session.add(customer)
    session.commit()
    
    print(f"[UPGRADE] Customer {customer_id} upgraded to paid plan, subscription ...{subscription_id[-4:]}")
    log_stripe_event("customer_upgraded", {
        "customer_id": customer_id,
        "stripe_customer_id": stripe_customer_id,
        "subscription_id": subscription_id
    })
    
    return {
        "success": True,
        "message": "Upgraded to paid plan - $99/month subscription active",
        "customer_id": customer_id,
        "plan": "paid",
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": subscription_id
    }


@app.post("/stripe/subscription-webhook")
async def stripe_subscription_webhook(request: Request, session: Session = Depends(get_session)):
    """
    Handle Stripe subscription webhook events.
    
    Validates webhook signature and processes subscription events.
    Updates customer plan status based on subscription changes.
    
    Supported events:
      - checkout.session.completed: Customer completed Stripe Checkout
      - invoice.payment_succeeded: Subscription payment succeeded
      - customer.subscription.updated: Subscription status changed
      - customer.subscription.deleted: Subscription canceled
    
    Returns:
      200 with status on success
      400 on invalid signature
    """
    try:
        payload = await request.body()
    except Exception as e:
        print(f"[STRIPE][SUBSCRIPTION-WEBHOOK] Failed to read request body: {e}")
        raise HTTPException(status_code=400, detail="Invalid request body")
    
    signature = request.headers.get("Stripe-Signature", "")
    
    if not signature:
        print("[STRIPE][SUBSCRIPTION-WEBHOOK] Missing Stripe-Signature header")
        log_stripe_event("subscription_webhook_missing_signature", {})
        raise HTTPException(status_code=400, detail="Missing signature header")
    
    webhook_secret = get_stripe_webhook_secret()
    if not webhook_secret:
        print("[STRIPE][SUBSCRIPTION-WEBHOOK] No webhook secret - accepting unverified")
    elif not verify_webhook_signature(payload, signature):
        print("[STRIPE][SUBSCRIPTION-WEBHOOK] Invalid signature - rejecting")
        log_stripe_event("subscription_webhook_invalid_signature", {})
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    try:
        event = json.loads(payload)
        event_type = event.get("type", "unknown")
        event_id = event.get("id", "unknown")
        event_data = event.get("data", {}).get("object", {})
        
        print(f"[STRIPE][SUBSCRIPTION-WEBHOOK] Received: {event_type} (id={event_id})")
        
        result = process_subscription_webhook(event_type, event_data)
        
        if result.success and result.customer_id:
            customer = session.exec(
                select(Customer).where(Customer.id == result.customer_id)
            ).first()
            
            if customer:
                if result.action == "subscription_canceled":
                    customer = expire_trial(customer)
                    print(f"[STRIPE][SUBSCRIPTION-WEBHOOK] Customer {customer.id} subscription canceled - plan set to trial_expired")
                elif result.new_status == "active":
                    customer.subscription_status = "active"
                    if customer.plan != "paid":
                        customer.plan = "paid"
                        print(f"[STRIPE][SUBSCRIPTION-WEBHOOK] Customer {customer.id} set to paid")
                elif result.new_status in ["past_due", "canceled", "unpaid"]:
                    customer.subscription_status = result.new_status
                    print(f"[STRIPE][SUBSCRIPTION-WEBHOOK] Customer {customer.id} subscription status: {result.new_status}")
                
                session.add(customer)
                session.commit()
        
        return {
            "status": "processed",
            "event_type": event_type,
            "event_id": event_id,
            "action": result.action,
            "customer_id": result.customer_id,
            "new_status": result.new_status
        }
        
    except json.JSONDecodeError as e:
        print(f"[STRIPE][SUBSCRIPTION-WEBHOOK] Invalid JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        print(f"[STRIPE][SUBSCRIPTION-WEBHOOK] Error: {e}")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": "Processing failed but acknowledged"}
        )


# ============================================================================
# RELEASE MODE & SUMMARY
# ============================================================================


@app.get("/api/release-mode")
def get_release_mode_endpoint():
    """Get current release mode configuration status."""
    return get_release_mode_status()


@app.get("/admin/summary")
def get_admin_summary(
    hours: int = Query(default=24, ge=1, le=168),
    session: Session = Depends(get_session)
):
    """
    Get summary of system activity for the last N hours.
    
    Default 24 hours, max 168 (one week).
    
    Returns aggregated stats for leads, emails, tasks, invoices, and payments.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    
    leads_new = session.exec(
        select(func.count()).select_from(Lead).where(Lead.created_at >= cutoff)
    ).one()
    leads_contacted = session.exec(
        select(func.count()).select_from(Lead).where(
            (Lead.status == "contacted") & (Lead.created_at >= cutoff)
        )
    ).one()
    leads_converted = session.exec(
        select(func.count()).select_from(Lead).where(
            (Lead.status == "converted") & (Lead.created_at >= cutoff)
        )
    ).one()
    leads_failed = session.exec(
        select(func.count()).select_from(Lead).where(
            (Lead.status == "email_failed") & (Lead.created_at >= cutoff)
        )
    ).one()
    
    tasks_completed = session.exec(
        select(func.count()).select_from(Task).where(
            (Task.status == "completed") & (Task.created_at >= cutoff)
        )
    ).one()
    tasks_profit = session.exec(
        select(func.coalesce(func.sum(Task.profit_cents), 0)).select_from(Task).where(
            (Task.status == "completed") & (Task.created_at >= cutoff)
        )
    ).one()
    
    invoices_generated = session.exec(
        select(func.count()).select_from(Invoice).where(Invoice.created_at >= cutoff)
    ).one()
    invoices_paid = session.exec(
        select(func.count()).select_from(Invoice).where(
            (Invoice.status == "paid") & (Invoice.paid_at >= cutoff)
        )
    ).one()
    revenue_cents = session.exec(
        select(func.coalesce(func.sum(Invoice.amount_cents), 0)).select_from(Invoice).where(
            (Invoice.status == "paid") & (Invoice.paid_at >= cutoff)
        )
    ).one()
    
    email_log = get_email_log(100)
    emails_in_period = [e for e in email_log if datetime.fromisoformat(e.get("timestamp", "2000-01-01")) >= cutoff]
    emails_sent = len([e for e in emails_in_period if e.get("status") == "sent"])
    emails_failed = len([e for e in emails_in_period if e.get("status") == "failed"])
    emails_dry_run = len([e for e in emails_in_period if e.get("mode") == "dry_run"])
    
    return {
        "period": {
            "hours": hours,
            "start": cutoff.isoformat(),
            "end": datetime.utcnow().isoformat()
        },
        "leads": {
            "new": leads_new,
            "contacted": leads_contacted,
            "converted": leads_converted,
            "email_failed": leads_failed
        },
        "emails": {
            "sent": emails_sent,
            "failed": emails_failed,
            "dry_run": emails_dry_run
        },
        "tasks": {
            "completed": tasks_completed,
            "profit_cents": tasks_profit
        },
        "invoices": {
            "generated": invoices_generated,
            "paid": invoices_paid,
            "revenue_cents": revenue_cents
        },
        "release_mode": is_release_mode(),
        "generated_at": datetime.utcnow().isoformat()
    }


# ============================================================================
# CUSTOMER PORTAL HELPER
# ============================================================================


def render_customer_portal(customer: Customer, request: Request, session: Session) -> HTMLResponse:
    """
    Render customer portal for a given customer.
    
    Helper function used by both session-based and token-based portal routes.
    """
    tasks = session.exec(
        select(Task).where(Task.customer_id == customer.id).order_by(Task.created_at.desc()).limit(20)
    ).all()
    
    invoices = session.exec(
        select(Invoice).where(Invoice.customer_id == customer.id).order_by(Invoice.created_at.desc())
    ).all()
    
    MIN_INVOICE_DISPLAY_CENTS = 1000
    displayable_invoices = [i for i in invoices if i.amount_cents >= MIN_INVOICE_DISPLAY_CENTS]
    outstanding_invoices = [i for i in displayable_invoices if i.status in ["draft", "sent"]]
    paid_invoices = [i for i in displayable_invoices if i.status == "paid"]
    total_invoiced = sum(i.amount_cents for i in invoices)
    total_paid = sum(i.amount_cents for i in paid_invoices)
    total_outstanding = sum(i.amount_cents for i in outstanding_invoices)
    
    payment_status = get_stripe_payment_mode_status()
    show_pay_buttons = payment_status["show_pay_buttons"]
    raw_payment_message = payment_status["status_message"]
    
    if raw_payment_message:
        payment_message = f'<div class="payment-notice">{raw_payment_message}</div>'
    else:
        payment_message = ""
    
    plan_status = get_customer_plan_status(customer)
    
    pending_outreach = session.exec(
        select(PendingOutbound).where(
            PendingOutbound.customer_id == customer.id,
            PendingOutbound.status == "PENDING"
        ).order_by(PendingOutbound.created_at.desc()).limit(20)
    ).all()
    
    if pending_outreach and customer.outreach_mode == "REVIEW":
        import html as html_module
        outreach_cards = ""
        for po in pending_outreach:
            timestamp = po.created_at.strftime("%Y-%m-%d %H:%M") if po.created_at else "-"
            context_truncated = (po.context_summary[:100] + "...") if po.context_summary and len(po.context_summary) > 100 else (po.context_summary or "")
            body_escaped = html_module.escape(po.body or "") if po.body else ""
            outreach_cards += f"""
                <div class="outreach-card">
                    <div class="outreach-header">
                        <div class="outreach-to">To: {html_module.escape(po.to_email)}</div>
                        <div class="outreach-date">{timestamp}</div>
                    </div>
                    <div class="outreach-subject"><strong>Subject:</strong> {html_module.escape(po.subject or "")}</div>
                    <div class="outreach-context">{html_module.escape(context_truncated)}</div>
                    <div class="outreach-actions">
                        <button class="outreach-btn approve" onclick="handleOutreach({po.id}, 'approve')">Approve &amp; Send</button>
                        <button class="outreach-btn edit" onclick="handleOutreach({po.id}, 'edit')">Edit &amp; Send</button>
                        <button class="outreach-btn skip" onclick="handleOutreach({po.id}, 'skip')">Skip</button>
                        <button class="outreach-btn view-message" onclick="toggleMessageBody(this, {po.id})">View Full Message</button>
                    </div>
                    <div class="outreach-body-preview" id="message-body-{po.id}">
                        <div class="outreach-body-content">{body_escaped}</div>
                    </div>
                </div>
            """
        
        pending_outreach_section = f"""
        <div class="section">
            <div class="section-header">
                <div class="section-title">Pending Outreach</div>
            </div>
            <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 1rem;">Review and approve outbound emails before they are sent</div>
            {outreach_cards}
        </div>
        <script>
        async function handleOutreach(id, action) {{
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Processing...';
            try {{
                const res = await fetch('/api/outreach/' + id + '/' + action, {{ method: 'POST' }});
                const data = await res.json();
                if (data.success) {{
                    btn.closest('.outreach-card').style.opacity = '0.5';
                    btn.closest('.outreach-card').style.pointerEvents = 'none';
                    setTimeout(() => location.reload(), 500);
                }} else {{
                    alert(data.error || 'Action failed');
                    btn.disabled = false;
                    btn.textContent = action === 'approve' ? 'Approve & Send' : action === 'edit' ? 'Edit & Send' : 'Skip';
                }}
            }} catch (e) {{
                alert('Error: ' + e.message);
                btn.disabled = false;
            }}
        }}
        </script>
        """
    else:
        pending_outreach_section = ""
    
    opportunities = get_todays_opportunities(session, company_id=customer.id, limit=10)
    
    if opportunities:
        opportunities_rows = ""
        for opp in opportunities:
            urgency_class = "urgency-high" if opp.urgency_score >= 70 else "urgency-medium" if opp.urgency_score >= 50 else "urgency-low"
            fire_icon = '<span class="fire-icon">🔥</span>' if opp.urgency_score >= 70 else ''
            timestamp = opp.created_at.strftime("%Y-%m-%d %H:%M") if opp.created_at else "-"
            summary_truncated = opp.summary[:80] + "..." if len(opp.summary) > 80 else opp.summary
            status_class = opp.status.lower().replace("_", "-")
            opportunities_rows += f"""
                <tr class="opportunity-row" data-opportunity-id="{opp.id}" onclick="showOpportunityDetail({opp.id})">
                    <td>{summary_truncated}</td>
                    <td><span class="category-badge">{opp.category}</span></td>
                    <td><span class="{urgency_class}">{fire_icon}{opp.urgency_score}</span></td>
                    <td><span class="status-badge opp-status-{status_class}">{opp.status}</span></td>
                    <td>{timestamp}</td>
                </tr>
                <tr class="opportunity-detail-row" id="opp-detail-{opp.id}" style="display: none;">
                    <td colspan="5" class="opportunity-detail-cell">
                        <div class="opportunity-detail-content" id="opp-content-{opp.id}">
                            <div class="loading-indicator">Loading details...</div>
                        </div>
                    </td>
                </tr>
            """
        
        opportunities_section = f"""
        <div class="section">
            <div class="section-header">
                <div class="section-title">Today's Opportunities</div>
            </div>
            <div class="opportunities-subtitle">Automatically identified from public context signals • Click a row to see details</div>
            <div class="table-wrapper">
                <table class="opportunities-table" style="margin-top: 1rem;">
                    <thead>
                        <tr>
                            <th>Summary</th>
                            <th>Category</th>
                            <th>Urgency</th>
                            <th>Status</th>
                            <th>Timestamp</th>
                        </tr>
                    </thead>
                    <tbody>
                        {opportunities_rows}
                    </tbody>
                </table>
            </div>
        </div>
        """
    else:
        opportunities_section = ""
    
    reports = session.exec(
        select(Report).where(Report.customer_id == customer.id).order_by(Report.created_at.desc()).limit(10)
    ).all()
    
    if reports:
        report_cards = ""
        for report in reports:
            timestamp = report.created_at.strftime("%Y-%m-%d") if report.created_at else "-"
            description_text = report.description or ""
            content_text = report.content or ""
            report_cards += f"""
                <div class="report-card" onclick="toggleReport(this)">
                    <div class="report-header">
                        <div class="report-title">{report.title[:70]}{'...' if len(report.title) > 70 else ''}</div>
                        <div class="report-date">{timestamp} <span class="report-expand-icon">▼</span></div>
                    </div>
                    <span class="report-type">{report.report_type}</span>
                    <div class="report-description">{description_text[:150]}{'...' if len(description_text) > 150 else ''}</div>
                    <div class="report-content">{content_text}</div>
                </div>
            """
        
        reports_section = f"""
        <div class="section">
            <div class="section-header">
                <div class="section-title">Reports / Recent Work</div>
            </div>
            {report_cards}
        </div>
        """
    else:
        reports_section = ""
    
    if plan_status.is_paid:
        if customer.cancelled_at_period_end:
            cancellation_notice = """
            <div class="cancellation-notice">
                <p>Your subscription is set to cancel at the end of this billing period.<br>
                You won't be charged again, but you have full access until then.</p>
            </div>
            """
            buttons = f"""
            <div class="btn-group">
                <a href="/portal/reactivate" class="cta-btn success" onclick="event.preventDefault(); document.getElementById('reactivate-form').submit();">Reactivate Subscription</a>
                <a href="/billing/{customer.public_token}" class="cta-btn secondary">Manage Billing</a>
            </div>
            <form id="reactivate-form" action="/portal/reactivate" method="POST" style="display:none;"></form>
            """
        else:
            cancellation_notice = ""
            buttons = f"""
            <div class="btn-group">
                <a href="/billing/{customer.public_token}" class="cta-btn secondary">Manage Billing</a>
                <a href="/portal/cancel" class="cta-btn danger" onclick="event.preventDefault(); if(confirm('Are you sure you want to cancel your subscription? You will retain access until the end of your billing period.')) document.getElementById('cancel-form').submit();">Cancel Subscription</a>
            </div>
            <form id="cancel-form" action="/portal/cancel" method="POST" style="display:none;"></form>
            """
        
        plan_section = f"""
        <div class="plan-card">
            <div class="plan-name">HossAgent Pro</div>
            <div class="plan-price">$99/month</div>
            <div class="plan-status active">Active Subscription</div>
            <div class="trial-info">Full access to all HossAgent features</div>
            {cancellation_notice}
            {buttons}
        </div>
        """
    elif plan_status.is_expired:
        plan_section = f"""
        <div class="plan-card">
            <div class="plan-name">HossAgent Pro</div>
            <div class="plan-price">$99/month</div>
            <div class="plan-status expired">Trial Expired</div>
            <div class="trial-info">Your trial period has ended</div>
            <div class="usage-display">
                <div class="usage-item">
                    <div class="usage-label">Tasks Used</div>
                    <div class="usage-value danger">{plan_status.tasks_used}/{plan_status.tasks_limit}</div>
                </div>
                <div class="usage-item">
                    <div class="usage-label">Leads Used</div>
                    <div class="usage-value danger">{plan_status.leads_used}/{plan_status.leads_limit}</div>
                </div>
            </div>
            <a href="/subscribe/{customer.public_token}" class="cta-btn">Start Paid Subscription</a>
        </div>
        """
    else:
        tasks_class = "danger" if plan_status.tasks_used >= plan_status.tasks_limit else "warning" if plan_status.tasks_used >= plan_status.tasks_limit * 0.8 else ""
        leads_class = "danger" if plan_status.leads_used >= plan_status.leads_limit else "warning" if plan_status.leads_used >= plan_status.leads_limit * 0.8 else ""
        
        limit_warning = ""
        if not plan_status.can_run_tasks or not plan_status.can_generate_leads:
            limit_warning = '<div class="limit-warning">You have reached the limits of your free trial. Start your paid subscription to keep HossAgent working.</div>'
        
        plan_section = f"""
        <div class="plan-card">
            <div class="plan-name">HossAgent Pro</div>
            <div class="plan-price">$99/month</div>
            <div class="plan-status trial">Trial - {plan_status.days_remaining} days remaining</div>
            <div class="trial-info">Limited to {plan_status.tasks_limit} tasks and {plan_status.leads_limit} leads</div>
            <div class="usage-display">
                <div class="usage-item">
                    <div class="usage-label">Tasks Used</div>
                    <div class="usage-value {tasks_class}">{plan_status.tasks_used}/{plan_status.tasks_limit}</div>
                </div>
                <div class="usage-item">
                    <div class="usage-label">Leads Used</div>
                    <div class="usage-value {leads_class}">{plan_status.leads_used}/{plan_status.leads_limit}</div>
                </div>
                <div class="usage-item">
                    <div class="usage-label">Days Left</div>
                    <div class="usage-value">{plan_status.days_remaining}</div>
                </div>
            </div>
            {limit_warning}
            <a href="/subscribe/{customer.public_token}" class="cta-btn">Start Paid Subscription</a>
        </div>
        """
    
    with open("templates/customer_portal.html", "r") as f:
        template = f.read()
    
    tasks_rows = ""
    for t in tasks:
        status_class = t.status
        tasks_rows += f"""
            <tr>
                <td>{t.created_at.strftime("%Y-%m-%d")}</td>
                <td>{t.description[:60]}{'...' if len(t.description) > 60 else ''}</td>
                <td><span class="status-badge {status_class}">{t.status}</span></td>
            </tr>
        """
    if not tasks_rows:
        tasks_rows = '<tr><td colspan="3" class="empty">No tasks yet.</td></tr>'
    
    stripe_enabled = is_stripe_enabled()
    
    outstanding_rows = ""
    for i in outstanding_invoices:
        payment_btn = ""
        if not plan_status.can_use_billing:
            payment_btn = '<span class="payment-unavailable">Upgrade to enable payments</span>'
        elif i.payment_url and len(i.payment_url) > 10:
            payment_btn = f'<a href="{i.payment_url}" class="pay-btn" target="_blank">PAY NOW</a>'
        elif stripe_enabled:
            payment_btn = '<span class="payment-unavailable">Awaiting payment link...</span>'
        
        outstanding_rows += f"""
            <tr>
                <td>INV-{i.id}</td>
                <td>${i.amount_cents/100:.2f}</td>
                <td><span class="status-badge draft">{i.status.upper()}</span></td>
                <td>{i.created_at.strftime("%Y-%m-%d")}</td>
                <td>{payment_btn}</td>
            </tr>
        """
    if not outstanding_rows:
        outstanding_rows = '<tr><td colspan="5" class="empty">No outstanding invoices.</td></tr>'
    
    paid_rows = ""
    for i in paid_invoices:
        paid_rows += f"""
            <tr>
                <td>INV-{i.id}</td>
                <td>${i.amount_cents/100:.2f}</td>
                <td><span class="status-badge paid">PAID</span></td>
                <td>{i.paid_at.strftime("%Y-%m-%d") if i.paid_at else '-'}</td>
            </tr>
        """
    if not paid_rows:
        paid_rows = '<tr><td colspan="4" class="empty">No paid invoices yet.</td></tr>'
    
    invoices_section = f"""
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Invoice</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Date</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {outstanding_rows}
                </tbody>
            </table>
        </div>
        <h4 style="font-size: 0.8rem; font-weight: normal; letter-spacing: 1px; color: #666; text-transform: uppercase; margin: 1.5rem 0 1rem;">Payment History</h4>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Invoice</th>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Paid Date</th>
                    </tr>
                </thead>
                <tbody>
                    {paid_rows}
                </tbody>
            </table>
        </div>
    """
    
    payment_banner = ""
    query_params = dict(request.query_params) if hasattr(request, 'query_params') else {}
    if query_params.get("payment") == "success":
        payment_banner = '<div class="payment-success">Payment successful! Your subscription is now active.</div>'
    elif query_params.get("payment") == "cancelled":
        payment_banner = '<div class="payment-cancelled">Payment was cancelled. You can try again when ready.</div>'
    elif query_params.get("cancelled") == "true":
        payment_banner = '<div class="payment-cancelled">Your subscription will remain active until the end of this billing period. You won\'t be charged again.</div>'
    elif query_params.get("reactivated") == "true":
        payment_banner = '<div class="payment-success">Your subscription has been reactivated. Thank you for staying with us!</div>'
    
    html = template.format(
        customer_id=customer.id,
        tasks_rows=tasks_rows,
        plan_section=plan_section,
        invoices_section=invoices_section,
        payment_message=payment_banner,
        opportunities_section=opportunities_section,
        pending_outreach_section=pending_outreach_section,
        reports_section=reports_section
    )
    
    return HTMLResponse(content=html)


# ============================================================================
# CUSTOMER PORTAL - TOKEN BASED ACCESS
# ============================================================================


@app.get("/portal/{public_token}", response_class=HTMLResponse)
def customer_portal_token(public_token: str, request: Request, session: Session = Depends(get_session)):
    """
    Token-based customer portal for admin impersonation or direct link access.
    
    Shows:
    - Account summary (total invoiced, paid, outstanding)
    - Customer info
    - Recent tasks
    - Outstanding invoices with PAY NOW buttons
    - Paid invoices
    - Payment status messaging based on Stripe configuration
    """
    customer = session.exec(
        select(Customer).where(Customer.public_token == public_token)
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Portal not found")
    
    return render_customer_portal(customer, request, session)


@app.get("/subscribe/{public_token}")
def subscribe_redirect(public_token: str, request: Request, session: Session = Depends(get_session)):
    """
    Redirect customer to Stripe Checkout for subscription.
    
    If Stripe is not configured, shows a friendly message.
    If already subscribed, redirects to billing portal.
    """
    customer = session.exec(
        select(Customer).where(Customer.public_token == public_token)
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    plan_status = get_customer_plan_status(customer)
    
    if plan_status.is_paid:
        success, portal_url, mode, error = create_billing_portal_link(
            customer,
            return_url=str(request.url_for("customer_portal_token", public_token=public_token))
        )
        if success and portal_url:
            return RedirectResponse(url=portal_url, status_code=303)
        else:
            return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Billing Portal</title>
    <style>
        body {{ background: #0a0a0a; color: #fff; font-family: Georgia, serif; 
               display: flex; align-items: center; justify-content: center; 
               min-height: 100vh; margin: 0; }}
        .box {{ text-align: center; padding: 3rem; border: 1px solid #333; max-width: 500px; }}
        h1 {{ font-size: 1.5rem; font-weight: normal; margin-bottom: 1rem; }}
        p {{ color: #888; margin-bottom: 1.5rem; }}
        a {{ display: inline-block; background: #fff; color: #0a0a0a; padding: 0.75rem 2rem; 
             text-decoration: none; font-weight: bold; }}
        a:hover {{ background: #ddd; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>Billing Portal Unavailable</h1>
        <p>The billing management portal is not currently available. Please contact support for billing inquiries.</p>
        <a href="/portal/{public_token}">Return to Portal</a>
    </div>
</body>
</html>
""", status_code=200)
    
    base_url = str(request.base_url).rstrip("/")
    success_url = f"{base_url}/portal/{public_token}?payment=success"
    cancel_url = f"{base_url}/portal/{public_token}?payment=cancelled"
    
    success, checkout_url, mode, error = get_or_create_subscription_checkout_link(
        customer,
        success_url=success_url,
        cancel_url=cancel_url
    )
    
    if success and checkout_url:
        return RedirectResponse(url=checkout_url, status_code=303)
    
    error_message = error or "Online billing is not currently configured."
    if mode == "disabled":
        error_message = "Online payment is not yet configured. Your account manager will be in touch to set up billing."
    
    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Subscribe - HossAgent</title>
    <style>
        body {{ background: #0a0a0a; color: #fff; font-family: Georgia, serif; 
               display: flex; align-items: center; justify-content: center; 
               min-height: 100vh; margin: 0; }}
        .box {{ text-align: center; padding: 3rem; border: 1px solid #333; max-width: 500px; }}
        h1 {{ font-size: 1.5rem; font-weight: normal; margin-bottom: 1rem; }}
        .price {{ font-size: 2rem; font-weight: bold; margin: 1rem 0; }}
        p {{ color: #888; margin-bottom: 1.5rem; }}
        a {{ display: inline-block; background: #fff; color: #0a0a0a; padding: 0.75rem 2rem; 
             text-decoration: none; font-weight: bold; }}
        a:hover {{ background: #ddd; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>HossAgent Pro</h1>
        <div class="price">$99/month</div>
        <p>{error_message}</p>
        <a href="/portal/{public_token}">Return to Portal</a>
    </div>
</body>
</html>
""", status_code=200)


@app.get("/billing/{public_token}")
def billing_portal_redirect(public_token: str, request: Request, session: Session = Depends(get_session)):
    """
    Redirect paid customers to Stripe Customer Portal for billing management.
    """
    customer = session.exec(
        select(Customer).where(Customer.public_token == public_token)
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    plan_status = get_customer_plan_status(customer)
    
    if not plan_status.is_paid:
        return RedirectResponse(url=f"/subscribe/{public_token}", status_code=303)
    
    success, portal_url, mode, error = create_billing_portal_link(
        customer,
        return_url=str(request.url_for("customer_portal_token", public_token=public_token))
    )
    
    if success and portal_url:
        return RedirectResponse(url=portal_url, status_code=303)
    
    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Billing Portal</title>
    <style>
        body {{ background: #0a0a0a; color: #fff; font-family: Georgia, serif; 
               display: flex; align-items: center; justify-content: center; 
               min-height: 100vh; margin: 0; }}
        .box {{ text-align: center; padding: 3rem; border: 1px solid #333; max-width: 500px; }}
        h1 {{ font-size: 1.5rem; font-weight: normal; margin-bottom: 1rem; }}
        p {{ color: #888; margin-bottom: 1.5rem; }}
        a {{ display: inline-block; background: #fff; color: #0a0a0a; padding: 0.75rem 2rem; 
             text-decoration: none; font-weight: bold; }}
        a:hover {{ background: #ddd; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>Billing Portal</h1>
        <p>The billing portal is not currently available. Please contact support for billing inquiries.</p>
        <a href="/portal/{public_token}">Return to Portal</a>
    </div>
</body>
</html>
""", status_code=200)


# ============================================================================
# CHECKOUT AND BILLING API ENDPOINTS
# ============================================================================


@app.post("/api/create-checkout-session")
def api_create_checkout_session(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Create a Stripe Checkout session for subscription.
    
    Requires authenticated customer (session cookie).
    Returns JSON with checkout URL.
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return JSONResponse(
            status_code=401,
            content={"error": "Not authenticated", "redirect": "/login"}
        )
    
    plan_status = get_customer_plan_status(customer)
    
    if plan_status.is_paid:
        return JSONResponse(
            status_code=400,
            content={"error": "Already subscribed", "redirect": f"/billing/{customer.public_token}"}
        )
    
    base_url = str(request.base_url).rstrip("/")
    success_url = f"{base_url}/portal?payment=success"
    cancel_url = f"{base_url}/portal?payment=cancelled"
    
    success, checkout_url, mode, error = get_or_create_subscription_checkout_link(
        customer,
        success_url=success_url,
        cancel_url=cancel_url
    )
    
    if success and checkout_url:
        return JSONResponse(content={"checkout_url": checkout_url, "mode": mode})
    
    return JSONResponse(
        status_code=400,
        content={"error": error or "Failed to create checkout session", "mode": mode}
    )


@app.post("/api/create-billing-portal-session")
def api_create_billing_portal_session(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Create a Stripe Billing Portal session for subscription management.
    
    Requires authenticated customer (session cookie) with paid subscription.
    Returns JSON with portal URL.
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token)
    
    if not customer:
        return JSONResponse(
            status_code=401,
            content={"error": "Not authenticated", "redirect": "/login"}
        )
    
    plan_status = get_customer_plan_status(customer)
    
    if not plan_status.is_paid:
        return JSONResponse(
            status_code=400,
            content={"error": "No active subscription", "redirect": f"/subscribe/{customer.public_token}"}
        )
    
    base_url = str(request.base_url).rstrip("/")
    return_url = f"{base_url}/portal"
    
    success, portal_url, mode, error = create_billing_portal_link(
        customer,
        return_url=return_url
    )
    
    if success and portal_url:
        return JSONResponse(content={"portal_url": portal_url, "mode": mode})
    
    return JSONResponse(
        status_code=400,
        content={"error": error or "Failed to create billing portal session", "mode": mode}
    )


# ============================================================================
# BIZDEV TEMPLATE STATUS API
# ============================================================================


@app.get("/api/bizdev/templates")
def get_bizdev_templates():
    """Get current BizDev template configuration and recent generations."""
    from bizdev_templates import get_template_status, get_template_log
    
    status = get_template_status()
    recent = get_template_log(10)
    
    return {
        **status,
        "recent_generations": recent
    }


# ============================================================================
# KPI DASHBOARD API
# ============================================================================


@app.get("/api/kpis")
def get_kpis(request: Request, session: Session = Depends(get_session)):
    """
    Get KPI counts for today's activity.
    
    Returns:
        - signals_today: Count of signals created today
        - lead_events_today: Count of lead events created today
        - outbound_sent_today: Count of outbound with APPROVED/SENT status updated today
        - reports_delivered_today: Count of reports created today
        - errors_failed: Count of pending outbound with FAILED or SKIPPED status
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    signals_today = session.exec(
        select(func.count()).select_from(Signal).where(Signal.created_at >= today_start)
    ).one()
    
    lead_events_today = session.exec(
        select(func.count()).select_from(LeadEvent).where(LeadEvent.created_at >= today_start)
    ).one()
    
    outbound_sent_today = session.exec(
        select(func.count()).select_from(PendingOutbound).where(
            (PendingOutbound.status.in_(["APPROVED", "SENT"])) &
            (PendingOutbound.created_at >= today_start)
        )
    ).one()
    
    reports_delivered_today = session.exec(
        select(func.count()).select_from(Report).where(Report.created_at >= today_start)
    ).one()
    
    errors_failed = session.exec(
        select(func.count()).select_from(PendingOutbound).where(
            PendingOutbound.status.in_(["FAILED", "SKIPPED"])
        )
    ).one()
    
    return {
        "signals_today": signals_today,
        "lead_events_today": lead_events_today,
        "outbound_sent_today": outbound_sent_today,
        "reports_delivered_today": reports_delivered_today,
        "errors_failed": errors_failed
    }


@app.get("/api/lead_events_detailed")
def get_lead_events_detailed(
    request: Request,
    limit: int = Query(default=50, le=100),
    session: Session = Depends(get_session)
):
    """
    Get detailed lead events with related outbound and report info.
    
    Returns lead events with has_outbound and has_report flags.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    events = session.exec(
        select(LeadEvent).order_by(LeadEvent.created_at.desc()).limit(limit)
    ).all()
    
    result = []
    for e in events:
        has_outbound = session.exec(
            select(func.count()).select_from(PendingOutbound).where(
                PendingOutbound.lead_event_id == e.id
            )
        ).one() > 0
        
        has_report = session.exec(
            select(func.count()).select_from(Report).where(
                Report.lead_id == e.lead_id
            )
        ).one() > 0 if e.lead_id else False
        
        company = None
        if e.company_id:
            customer = session.exec(
                select(Customer).where(Customer.id == e.company_id)
            ).first()
            company = customer.company if customer else None
        
        result.append({
            "id": e.id,
            "summary": e.summary,
            "category": e.category,
            "urgency_score": e.urgency_score,
            "status": e.status,
            "has_outbound": has_outbound,
            "has_report": has_report,
            "company": company,
            "signal_id": e.signal_id,
            "lead_id": e.lead_id,
            "created_at": e.created_at.isoformat() if e.created_at else None
        })
    
    return result


@app.get("/api/output_history")
def get_output_history(
    request: Request,
    limit: int = Query(default=50, le=100),
    session: Session = Depends(get_session)
):
    """
    Get combined output history: outbound messages and reports.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    outbound = session.exec(
        select(PendingOutbound).order_by(PendingOutbound.created_at.desc()).limit(limit)
    ).all()
    
    reports = session.exec(
        select(Report).order_by(Report.created_at.desc()).limit(limit)
    ).all()
    
    outbound_list = []
    for o in outbound:
        outbound_list.append({
            "id": o.id,
            "lead_event_id": o.lead_event_id,
            "to_email": o.to_email,
            "subject": o.subject,
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "sent_at": o.sent_at.isoformat() if o.sent_at else None
        })
    
    reports_list = []
    for r in reports:
        lead_event = None
        if r.lead_id:
            le = session.exec(
                select(LeadEvent).where(LeadEvent.lead_id == r.lead_id)
            ).first()
            lead_event = le.id if le else None
        
        reports_list.append({
            "id": r.id,
            "title": r.title,
            "lead_event_id": lead_event,
            "report_type": r.report_type,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    
    return {
        "outbound": outbound_list,
        "reports": reports_list
    }


# ============================================================================
# OPPORTUNITY DETAIL API - CUSTOMER PORTAL
# ============================================================================


@app.get("/api/opportunity/{opportunity_id}/detail")
def get_opportunity_detail(
    opportunity_id: int,
    request: Request,
    session: Session = Depends(get_session),
    customer_id: Optional[int] = Query(None, description="Customer ID for portal access")
):
    """
    Get detailed opportunity (LeadEvent) data for customer portal.
    
    Returns:
    - Full LeadEvent data (summary, category, urgency, status, lifecycle info)
    - Related Signal context (if available)
    - Related PendingOutbound records
    - Related Report records
    - Lead info (if linked)
    
    Authenticated via customer session cookie OR customer_id parameter.
    The customer_id parameter is used when viewing portal via admin token.
    """
    customer = None
    
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        customer = get_customer_from_session(session, session_token)
    
    if not customer and customer_id:
        customer = session.exec(select(Customer).where(Customer.id == customer_id)).first()
    
    if not customer:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    opportunity = session.exec(
        select(LeadEvent).where(
            LeadEvent.id == opportunity_id,
            LeadEvent.company_id == customer.id
        )
    ).first()
    
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    
    signal_data = None
    if opportunity.signal_id:
        signal = session.exec(
            select(Signal).where(Signal.id == opportunity.signal_id)
        ).first()
        if signal:
            signal_data = {
                "id": signal.id,
                "source_type": signal.source_type,
                "context_summary": signal.context_summary,
                "geography": signal.geography,
                "created_at": signal.created_at.isoformat() if signal.created_at else None
            }
    
    outbound_records = session.exec(
        select(PendingOutbound).where(
            PendingOutbound.lead_event_id == opportunity.id
        ).order_by(PendingOutbound.created_at.desc())
    ).all()
    
    outbound_list = []
    for o in outbound_records:
        outbound_list.append({
            "id": o.id,
            "to_email": o.to_email,
            "to_name": o.to_name,
            "subject": o.subject,
            "body": o.body,
            "context_summary": o.context_summary,
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "sent_at": o.sent_at.isoformat() if o.sent_at else None,
            "approved_at": o.approved_at.isoformat() if o.approved_at else None
        })
    
    report_records = session.exec(
        select(Report).where(
            (Report.lead_event_id == opportunity.id) |
            (Report.lead_id == opportunity.lead_id)
        ).order_by(Report.created_at.desc())
    ).all() if opportunity.lead_id else session.exec(
        select(Report).where(Report.lead_event_id == opportunity.id).order_by(Report.created_at.desc())
    ).all()
    
    reports_list = []
    for r in report_records:
        reports_list.append({
            "id": r.id,
            "title": r.title,
            "description": r.description,
            "content": r.content,
            "report_type": r.report_type,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    
    lead_data = None
    if opportunity.lead_id:
        lead = session.exec(
            select(Lead).where(Lead.id == opportunity.lead_id)
        ).first()
        if lead:
            lead_data = {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "company": lead.company,
                "niche": lead.niche,
                "status": lead.status,
                "website": lead.website,
                "source": lead.source
            }
    
    return {
        "id": opportunity.id,
        "summary": opportunity.summary,
        "category": opportunity.category,
        "urgency_score": opportunity.urgency_score,
        "status": opportunity.status,
        "recommended_action": opportunity.recommended_action,
        "outbound_message": opportunity.outbound_message,
        "last_contact_at": opportunity.last_contact_at.isoformat() if opportunity.last_contact_at else None,
        "last_contact_summary": opportunity.last_contact_summary,
        "next_step": opportunity.next_step,
        "next_step_owner": opportunity.next_step_owner,
        "created_at": opportunity.created_at.isoformat() if opportunity.created_at else None,
        "signal": signal_data,
        "outbound_messages": outbound_list,
        "reports": reports_list,
        "lead": lead_data
    }


# ============================================================================
# LEAD EVENT DETAIL API - ADMIN CONSOLE
# ============================================================================


@app.get("/api/admin/lead_event/{event_id}/detail")
def get_lead_event_detail_admin(
    event_id: int,
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Get detailed LeadEvent data for admin console.
    
    Returns:
    - Full LeadEvent data
    - Related Signal context
    - Related PendingOutbound records
    - Related Report records
    - Lead info (if linked)
    
    Authenticated via admin session cookie.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    event = session.exec(
        select(LeadEvent).where(LeadEvent.id == event_id)
    ).first()
    
    if not event:
        raise HTTPException(status_code=404, detail="Lead event not found")
    
    signal_data = None
    if event.signal_id:
        signal = session.exec(
            select(Signal).where(Signal.id == event.signal_id)
        ).first()
        if signal:
            signal_data = {
                "id": signal.id,
                "source_type": signal.source_type,
                "context_summary": signal.context_summary,
                "geography": signal.geography,
                "created_at": signal.created_at.isoformat() if signal.created_at else None
            }
    
    outbound_records = session.exec(
        select(PendingOutbound).where(
            PendingOutbound.lead_event_id == event.id
        ).order_by(PendingOutbound.created_at.desc())
    ).all()
    
    outbound_list = []
    for o in outbound_records:
        outbound_list.append({
            "id": o.id,
            "to_email": o.to_email,
            "to_name": o.to_name,
            "subject": o.subject,
            "body": o.body,
            "context_summary": o.context_summary,
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "sent_at": o.sent_at.isoformat() if o.sent_at else None
        })
    
    report_records = []
    if event.lead_id:
        report_records = session.exec(
            select(Report).where(Report.lead_id == event.lead_id).order_by(Report.created_at.desc())
        ).all()
    
    reports_list = []
    for r in report_records:
        reports_list.append({
            "id": r.id,
            "title": r.title,
            "description": r.description,
            "content": r.content,
            "report_type": r.report_type,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
    
    lead_data = None
    if event.lead_id:
        lead = session.exec(
            select(Lead).where(Lead.id == event.lead_id)
        ).first()
        if lead:
            lead_data = {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "company": lead.company,
                "niche": lead.niche,
                "status": lead.status,
                "website": lead.website,
                "source": lead.source
            }
    
    company_data = None
    if event.company_id:
        customer = session.exec(
            select(Customer).where(Customer.id == event.company_id)
        ).first()
        if customer:
            company_data = {
                "id": customer.id,
                "company": customer.company,
                "email": customer.contact_email
            }
    
    return {
        "id": event.id,
        "summary": event.summary,
        "category": event.category,
        "urgency_score": event.urgency_score,
        "status": event.status,
        "recommended_action": event.recommended_action,
        "outbound_message": event.outbound_message,
        "last_contact_at": event.last_contact_at.isoformat() if event.last_contact_at else None,
        "last_contact_summary": event.last_contact_summary,
        "next_step": event.next_step,
        "next_step_owner": event.next_step_owner,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "signal": signal_data,
        "outbound_messages": outbound_list,
        "reports": reports_list,
        "lead": lead_data,
        "company": company_data
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
