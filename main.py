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
from models import (
    Lead, Customer, Task, Invoice, SystemSettings, TrialIdentity, 
    Signal, LeadEvent, PasswordResetToken, PendingOutbound, BusinessProfile, Report,
    Thread, Message, Suppression, ConversationMetrics,
    THREAD_STATUS_OPEN, THREAD_STATUS_HUMAN_OWNED, THREAD_STATUS_AUTO, THREAD_STATUS_CLOSED,
    MESSAGE_DIRECTION_INBOUND, MESSAGE_DIRECTION_OUTBOUND,
    MESSAGE_STATUS_QUEUED, MESSAGE_STATUS_SENT, MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_FAILED, MESSAGE_STATUS_APPROVED,
    MESSAGE_GENERATED_AI, MESSAGE_GENERATED_HUMAN, MESSAGE_GENERATED_SYSTEM
)
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
import signal_sources
from signal_sources import get_signal_status, run_signal_pipeline, get_registry, get_signal_mode
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
        
        if hasattr(lead, 'source') and lead.source in ("dummy_seed", "test", "demo"):
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
    import os
    print_startup_banners()
    
    create_db_and_tables()
    
    validate_stripe_at_startup()
    
    bootstrap_result = bootstrap_stripe_subscription_product()
    if bootstrap_result["success"]:
        print(f"[STARTUP] Subscription product ready: {bootstrap_result['message']}")
    elif is_stripe_enabled():
        print(f"[STARTUP] Subscription bootstrap: {bootstrap_result['message']}")
    
    run_retroactive_payment_links()
    
    apollo_key = os.getenv("APOLLO_API_KEY")
    if apollo_key:
        from apollo_integration import connect_apollo_with_key
        result = connect_apollo_with_key(apollo_key)
        if result.get("connected"):
            print("[APOLLO][STARTUP] Auto-connected from APOLLO_API_KEY secret")
        else:
            print(f"[APOLLO][STARTUP] Failed to auto-connect: {result.get('error', 'Unknown error')}")
    else:
        print("[APOLLO][STARTUP] APOLLO_API_KEY not set - lead generation paused until configured")
    
    asyncio.create_task(autopilot_loop())
    print("[STARTUP] HossAgent initialized. Autopilot loop active.")


async def autopilot_loop():
    """
    Background task: Runs agent cycles automatically when autopilot is enabled.
    
    Checks SystemSettings.autopilot_enabled every 15 minutes (production cycle).
    If enabled, runs the full SignalNet-first pipeline:
      1. Lead Generation - Fetch new leads from configured source
      2. SignalNet Pipeline - Fetch signals, score, convert to LeadEvents
      3. Enrichment - Enrich unenriched LeadEvents with contact data
      4. BizDev - Send outreach emails to NEW leads
      5. Event-Driven BizDev - Process ENRICHED LeadEvents for contextual outreach
      6. Onboarding - Convert qualified leads to customers
      7. Ops - Execute pending tasks and generate reports
      8. Billing - Generate invoices for completed work
    
    Pipeline: SignalNet → Score → LeadEvents → Enrich → BizDev → Email
    
    Per-customer autopilot settings override global behavior.
    Safe: Catches and logs exceptions without crashing the loop.
    Idempotent: Prevents duplicate LeadEvents, outbound, and reports.
    """
    # Allow the server to fully start before running the first cycle
    await asyncio.sleep(5)
    
    # Log email mode at startup
    from email_utils import get_email_status
    email_status = get_email_status()
    print("[AUTOPILOT][STARTUP] Pipeline: SignalNet → Score → LeadEvents → Enrich → BizDev → Email")
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
                    
                    # Step 1: Generate new leads from configured source
                    generate_new_leads_from_source(session)
                    
                    # Step 2-3: Run SignalNet pipeline (fetches, scores, converts to LeadEvents)
                    run_signals_agent(session)
                    
                    # Step 4: Enrich unenriched LeadEvents (batch of up to 15 per cycle)
                    try:
                        from lead_enrichment import run_enrichment_pipeline
                        enrichment_results = await run_enrichment_pipeline(session)
                        pending_info = f", Pending: {enrichment_results.get('pending', 0)}" if enrichment_results.get('pending', 0) > 0 else ""
                        print(f"[ENRICHMENT] Processed: {enrichment_results.get('processed', 0)}, "
                              f"Enriched: {enrichment_results.get('enriched', 0)}, "
                              f"Failed: {enrichment_results.get('failed', 0)}{pending_info}")
                    except Exception as e:
                        print(f"[ENRICHMENT][ERROR] {e}")
                    
                    # Step 5-6: BizDev outreach (now prefers enriched LeadEvents)
                    await run_bizdev_cycle(session)
                    await run_event_driven_bizdev_cycle(session)
                    
                    # Step 7: Onboarding, Ops, and Billing
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
    
    with open("templates/admin_console_new.html", "r") as f:
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
        - provider: Current provider (Apollo)
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
    owner_email_domain: str = Query(default="", description="Domain to identify real customers (e.g., 'hossagent.net')"),
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
        POST /admin/production-cleanup?owner_email_domain=hossagent.net&purge_all_signals=true&confirm=true
    
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
# CONVERSATION ENGINE ENDPOINTS
# ============================================================================


from conversation_engine import (
    validate_inbound_secret, parse_sendgrid_inbound, process_inbound_email,
    send_queued_messages, approve_draft, edit_and_approve_draft, discard_draft,
    set_thread_status, get_thread_summary, get_customer_threads, calculate_customer_metrics,
    THREAD_STATUS_OPEN, THREAD_STATUS_HUMAN_OWNED, THREAD_STATUS_AUTO, THREAD_STATUS_CLOSED,
    InboundEmailData
)


@app.post("/email/inbound")
async def inbound_email_webhook(request: Request, session: Session = Depends(get_session)):
    """
    Handle inbound email from SendGrid Inbound Parse webhook.
    
    Parses incoming email, matches to thread/customer, stores message,
    generates AI draft reply if applicable.
    
    Requires INBOUND_EMAIL_SECRET env var for validation (optional but recommended).
    """
    try:
        form_data = await request.form()
        request_data = {key: value for key, value in form_data.items()}
        
        provided_secret = request.headers.get("X-Inbound-Secret", "")
        if not provided_secret:
            provided_secret = request_data.get("secret", "")
        
        if not validate_inbound_secret(provided_secret):
            print("[INBOUND][WEBHOOK] Invalid secret")
            raise HTTPException(status_code=401, detail="Invalid secret")
        
        email_data = parse_sendgrid_inbound(request_data)
        
        print(f"[INBOUND][WEBHOOK] Received from {email_data.from_email} to {email_data.to_email}")
        print(f"[INBOUND][WEBHOOK] Subject: {email_data.subject}")
        
        result = process_inbound_email(session, email_data)
        
        if result["success"]:
            print(f"[INBOUND][WEBHOOK] Processed: thread={result['thread_id']}, message={result['message_id']}, actions={result['actions']}")
            return JSONResponse({
                "status": "processed",
                "thread_id": result["thread_id"],
                "message_id": result["message_id"],
                "actions": result["actions"]
            })
        else:
            print(f"[INBOUND][WEBHOOK] Failed: {result['error']}")
            return JSONResponse({
                "status": "failed",
                "error": result["error"],
                "actions": result.get("actions", [])
            }, status_code=200)
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"[INBOUND][WEBHOOK] Error: {e}")
        return JSONResponse({
            "status": "error",
            "error": str(e)
        }, status_code=500)


@app.get("/api/conversations/threads")
def api_get_threads(
    request: Request,
    customer_id: int = Query(None),
    status: str = Query(None),
    limit: int = Query(50),
    session: Session = Depends(get_session)
):
    """Get conversation threads, optionally filtered by customer and status."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if customer and not is_admin:
        customer_id = customer.id
    
    if customer_id:
        threads = get_customer_threads(session, customer_id, status, limit)
    else:
        query = select(Thread).order_by(Thread.updated_at.desc()).limit(limit)
        if status:
            query = query.where(Thread.status == status)
        all_threads = session.exec(query).all()
        threads = [
            {
                "id": t.id,
                "customer_id": t.customer_id,
                "lead_email": t.lead_email,
                "lead_name": t.lead_name,
                "status": t.status,
                "message_count": t.message_count,
                "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
                "last_direction": t.last_direction,
                "last_summary": t.last_summary
            }
            for t in all_threads
        ]
    
    return {"threads": threads, "count": len(threads)}


@app.get("/api/conversations/thread/{thread_id}")
def api_get_thread(request: Request, thread_id: int, session: Session = Depends(get_session)):
    """Get thread details with all messages."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    summary = get_thread_summary(session, thread_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if customer and not is_admin and summary.get("customer_id") != customer.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return summary


@app.post("/api/conversations/thread/{thread_id}/status")
def api_set_thread_status(
    request: Request,
    thread_id: int,
    status: str = Query(..., description="OPEN, HUMAN_OWNED, AUTO, or CLOSED"),
    session: Session = Depends(get_session)
):
    """Update thread status."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if status not in [THREAD_STATUS_OPEN, THREAD_STATUS_HUMAN_OWNED, THREAD_STATUS_AUTO, THREAD_STATUS_CLOSED]:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    thread = session.exec(select(Thread).where(Thread.id == thread_id)).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if customer and not is_admin and thread.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    success = set_thread_status(session, thread_id, status)
    if not success:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    return {"status": "updated", "thread_id": thread_id, "new_status": status}


@app.get("/api/conversations/drafts")
def api_get_drafts(
    request: Request,
    customer_id: int = Query(None),
    limit: int = Query(50),
    session: Session = Depends(get_session)
):
    """Get pending draft messages awaiting approval."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if customer and not is_admin:
        customer_id = customer.id
    
    query = select(Message).where(Message.status == MESSAGE_STATUS_DRAFT)
    if customer_id:
        query = query.where(Message.customer_id == customer_id)
    query = query.order_by(Message.created_at.desc()).limit(limit)
    
    drafts = session.exec(query).all()
    
    return {
        "drafts": [
            {
                "id": m.id,
                "thread_id": m.thread_id,
                "customer_id": m.customer_id,
                "to_email": m.to_email,
                "subject": m.subject,
                "body_text": m.body_text,
                "generated_by": m.generated_by,
                "guardrail_flags": json.loads(m.guardrail_flags) if m.guardrail_flags else None,
                "created_at": m.created_at.isoformat() if m.created_at else None
            }
            for m in drafts
        ],
        "count": len(drafts)
    }


@app.post("/api/conversations/draft/{message_id}/approve")
def api_approve_draft(request: Request, message_id: int, session: Session = Depends(get_session)):
    """Approve a draft message for sending."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    msg = session.exec(select(Message).where(Message.id == message_id)).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    if customer and not is_admin and msg.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    success = approve_draft(session, message_id)
    if not success:
        raise HTTPException(status_code=404, detail="Draft not found or already processed")
    return {"status": "approved", "message_id": message_id}


@app.post("/api/conversations/draft/{message_id}/edit")
def api_edit_draft(
    request: Request,
    message_id: int,
    body_text: str = Form(...),
    subject: str = Form(None),
    session: Session = Depends(get_session)
):
    """Edit and approve a draft message."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    msg = session.exec(select(Message).where(Message.id == message_id)).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    if customer and not is_admin and msg.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    success = edit_and_approve_draft(session, message_id, body_text, subject)
    if not success:
        raise HTTPException(status_code=404, detail="Draft not found or already processed")
    return {"status": "edited_and_approved", "message_id": message_id}


@app.post("/api/conversations/draft/{message_id}/discard")
def api_discard_draft(request: Request, message_id: int, session: Session = Depends(get_session)):
    """Discard a draft message."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    msg = session.exec(select(Message).where(Message.id == message_id)).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    if customer and not is_admin and msg.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    success = discard_draft(session, message_id)
    if not success:
        raise HTTPException(status_code=404, detail="Draft not found or already processed")
    return {"status": "discarded", "message_id": message_id}


@app.post("/api/conversations/send-queued")
def api_send_queued(
    request: Request,
    max_messages: int = Query(10),
    session: Session = Depends(get_session)
):
    """Send queued messages (admin only)."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    results = send_queued_messages(session, max_messages)
    return {
        "sent": len([r for r in results if r.get("status") == "sent"]),
        "failed": len([r for r in results if r.get("status") == "failed"]),
        "results": results
    }


@app.get("/api/conversations/metrics/{customer_id}")
def api_get_metrics(request: Request, customer_id: int, session: Session = Depends(get_session)):
    """Get conversation metrics for a customer."""
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    customer = get_customer_from_session(session, session_token) if session_token else None
    
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    is_admin = verify_admin_session(admin_token) if admin_token else False
    
    if not customer and not is_admin:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if customer and not is_admin and customer.id != customer_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    metrics = calculate_customer_metrics(session, customer_id)
    return {
        "customer_id": customer_id,
        "total_lead_events": metrics.total_lead_events,
        "total_threads": metrics.total_threads,
        "leads_contacted": metrics.leads_contacted,
        "leads_replied": metrics.leads_replied,
        "reply_rate_pct": round(metrics.reply_rate_pct, 1),
        "avg_response_time_seconds": metrics.avg_response_time_seconds,
        "total_outbound": metrics.total_outbound,
        "total_inbound": metrics.total_inbound,
        "messages_ai_drafted": metrics.messages_ai_drafted,
        "messages_human_sent": metrics.messages_human_sent,
        "avg_thread_depth": round(metrics.avg_thread_depth, 1),
        "last_calculated_at": metrics.last_calculated_at.isoformat() if metrics.last_calculated_at else None
    }


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
    
    Clean 3-section layout:
    1. Account Status (top card)
    2. Recent Opportunities & Outreach (combined view)
    3. Reports & Deep Dives
    """
    import html as html_module
    
    plan_status = get_customer_plan_status(customer)
    
    invoices = session.exec(
        select(Invoice).where(Invoice.customer_id == customer.id).order_by(Invoice.created_at.desc()).limit(5)
    ).all()
    outstanding_invoices = [i for i in invoices if i.status in ["draft", "sent"]]
    total_outstanding = sum(i.amount_cents for i in outstanding_invoices)
    
    if plan_status.is_paid:
        plan_name = "HossAgent Pro"
        status_class = "active"
        status_label = "Active"
        billing_info = "$99/month - Full access"
        if customer.cancelled_at_period_end:
            billing_info = "Cancels at end of billing period"
            account_cta = f'''<a href="/portal/reactivate" class="account-cta" onclick="event.preventDefault(); document.getElementById('reactivate-form').submit();">Reactivate</a>
            <a href="/billing/{customer.public_token}" class="account-cta secondary" style="margin-left: 0.5rem;">Manage Billing</a>
            <form id="reactivate-form" action="/portal/reactivate" method="POST" style="display:none;"></form>'''
        else:
            if total_outstanding > 0:
                billing_info = f"$99/month - ${total_outstanding/100:.0f} outstanding"
            account_cta = f'''<a href="/billing/{customer.public_token}" class="account-cta secondary">Manage Billing</a>
            <a href="/portal/cancel" class="account-cta secondary" style="margin-left: 0.5rem; color: var(--accent-red);" onclick="event.preventDefault(); if(confirm('Cancel your subscription? Access continues until end of billing period.')) document.getElementById('cancel-form').submit();">Cancel</a>
            <form id="cancel-form" action="/portal/cancel" method="POST" style="display:none;"></form>'''
    elif plan_status.is_expired:
        plan_name = "Trial"
        status_class = "paused"
        status_label = "Expired"
        billing_info = f"Trial ended - {plan_status.tasks_used}/{plan_status.tasks_limit} tasks, {plan_status.leads_used}/{plan_status.leads_limit} leads used"
        account_cta = f'''<a href="/subscribe/{customer.public_token}" class="account-cta">Start Subscription - $99/month</a>'''
    else:
        plan_name = "Trial"
        status_class = "trial"
        status_label = f"{plan_status.days_remaining} days left"
        limit_text = f"{plan_status.tasks_used}/{plan_status.tasks_limit} tasks, {plan_status.leads_used}/{plan_status.leads_limit} leads"
        billing_info = f"Trial ends in {plan_status.days_remaining} days ({limit_text})"
        account_cta = f'''<a href="/subscribe/{customer.public_token}" class="account-cta">Upgrade to Pro - $99/month</a>'''
    
    autopilot_class = "autopilot-on" if customer.autopilot_enabled else "autopilot-off"
    autopilot_label = "ON" if customer.autopilot_enabled else "OFF"
    
    payment_banner = ""
    query_params = dict(request.query_params) if hasattr(request, 'query_params') else {}
    if query_params.get("payment") == "success":
        payment_banner = '<div class="payment-success">Payment successful! Your subscription is now active.</div>'
    elif query_params.get("payment") == "cancelled":
        payment_banner = '<div class="payment-cancelled">Payment was cancelled. You can try again when ready.</div>'
    elif query_params.get("cancelled") == "true":
        payment_banner = '<div class="payment-cancelled">Your subscription will remain active until the end of this billing period.</div>'
    elif query_params.get("reactivated") == "true":
        payment_banner = '<div class="payment-success">Your subscription has been reactivated!</div>'
    
    total_opportunities = session.exec(select(func.count(LeadEvent.id)).where(LeadEvent.company_id == customer.id)).one()
    opportunities = session.exec(
        select(LeadEvent).where(LeadEvent.company_id == customer.id)
        .order_by(LeadEvent.created_at.desc()).limit(30)
    ).all()
    
    pending_outreach = session.exec(
        select(PendingOutbound).where(
            PendingOutbound.customer_id == customer.id,
            PendingOutbound.status == "PENDING"
        ).order_by(PendingOutbound.created_at.desc()).limit(10)
    ).all()
    pending_map = {po.lead_event_id: po for po in pending_outreach if po.lead_event_id}
    
    if opportunities:
        opp_cards = ""
        for opp in opportunities:
            timestamp = opp.created_at.strftime("%b %d") if opp.created_at else "-"
            company_name = html_module.escape(opp.lead_company or opp.summary[:40] or "Unknown Lead")
            signal_summary = html_module.escape(opp.summary[:120] if opp.summary else "Opportunity identified")
            
            if opp.status.upper() == "CONTACTED":
                status_class_opp = "sent"
                status_text = "Email Sent"
            elif opp.status.upper() == "RESPONDED":
                status_class_opp = "responded"
                status_text = "Responded"
            elif opp.do_not_contact:
                status_class_opp = "suppressed"
                status_text = "Suppressed"
            elif opp.status.upper() in ["CLOSED", "CLOSED_WON", "CLOSED_LOST"]:
                status_class_opp = "closed"
                status_text = "Closed"
            else:
                status_class_opp = "new"
                status_text = "New"
            
            outbound = session.exec(
                select(PendingOutbound).where(
                    PendingOutbound.lead_event_id == opp.id
                ).order_by(PendingOutbound.created_at.desc()).limit(1)
            ).first()
            
            email_detail = ""
            if outbound and outbound.status == "SENT":
                email_detail = f'''
                <div class="email-preview">
                    <div class="email-header">
                        <span class="email-to">To: {html_module.escape(outbound.to_email)}</span>
                        <span class="email-sent-badge">Sent</span>
                    </div>
                    <div class="email-subject">Subject: {html_module.escape(outbound.subject or "")}</div>
                    <div class="email-body">{html_module.escape(outbound.body or "")}</div>
                </div>
                '''
            elif outbound and outbound.status == "PENDING" and customer.outreach_mode == "REVIEW":
                email_detail = f'''
                <div class="email-preview" style="border-left-color: var(--accent-orange);">
                    <div class="email-header">
                        <span class="email-to">To: {html_module.escape(outbound.to_email)}</span>
                        <span class="email-sent-badge" style="background: rgba(245, 158, 11, 0.15); color: var(--accent-orange);">Awaiting Your Approval</span>
                    </div>
                    <div class="email-subject">Subject: {html_module.escape(outbound.subject or "")}</div>
                    <div class="email-body">{html_module.escape(outbound.body or "")}</div>
                    <div class="approval-actions" style="margin-top: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border-subtle); display: flex; gap: 0.5rem; flex-wrap: wrap;">
                        <button onclick="event.stopPropagation(); handleOutreach({outbound.id}, 'approve')" style="background: var(--accent-green); color: #000; border: none; padding: 0.5rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; font-weight: 500;">Approve & Send</button>
                        <button onclick="event.stopPropagation(); handleOutreach({outbound.id}, 'skip')" style="background: transparent; color: var(--text-secondary); border: 1px solid var(--border-medium); padding: 0.5rem 1rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem;">Skip</button>
                    </div>
                </div>
                '''
            elif outbound and outbound.status in ["APPROVED", "PENDING"]:
                status_badge = "Queued" if outbound.status == "APPROVED" else "Pending"
                email_detail = f'''
                <div class="email-preview" style="border-left-color: var(--accent-orange);">
                    <div class="email-header">
                        <span class="email-to">To: {html_module.escape(outbound.to_email)}</span>
                        <span class="email-sent-badge" style="background: rgba(245, 158, 11, 0.15); color: var(--accent-orange);">{status_badge}</span>
                    </div>
                    <div class="email-subject">Subject: {html_module.escape(outbound.subject or "")}</div>
                    <div class="email-body">{html_module.escape(outbound.body or "")}</div>
                </div>
                '''
            elif outbound and outbound.status == "SKIPPED":
                email_detail = f'''
                <div class="email-preview" style="border-left-color: var(--text-tertiary); opacity: 0.7;">
                    <div class="email-header">
                        <span class="email-to">To: {html_module.escape(outbound.to_email)}</span>
                        <span class="email-sent-badge" style="background: var(--bg-secondary); color: var(--text-tertiary);">Skipped</span>
                    </div>
                    <div class="email-subject">Subject: {html_module.escape(outbound.subject or "")}</div>
                    <div class="email-body">{html_module.escape(outbound.body or "")}</div>
                </div>
                '''
            else:
                email_detail = '<div class="no-email">No outbound email yet for this opportunity</div>'
            
            opp_cards += f'''
            <div class="opp-card" id="opp-{opp.id}" onclick="toggleOpp('opp-{opp.id}')">
                <div class="opp-row">
                    <div class="opp-main">
                        <div class="opp-company">{company_name}</div>
                        <div class="opp-signal">{signal_summary}</div>
                    </div>
                    <div class="opp-meta">
                        <span class="opp-date">{timestamp}</span>
                        <span class="opp-status {status_class_opp}">{status_text}</span>
                    </div>
                </div>
                <div class="opp-detail">
                    {email_detail}
                </div>
            </div>
            '''
        
        opportunities_content = opp_cards
    else:
        opportunities_content = '''
        <div class="empty-state">
            <div class="empty-state-title">No opportunities yet</div>
            <div class="empty-state-sub">HossAgent is monitoring signals and will identify opportunities for you.</div>
        </div>
        '''
    
    reports = session.exec(
        select(Report).where(Report.customer_id == customer.id).order_by(Report.created_at.desc()).limit(15)
    ).all()
    
    if reports:
        report_cards = ""
        for idx, report in enumerate(reports):
            timestamp = report.created_at.strftime("%b %d, %Y") if report.created_at else "-"
            title = html_module.escape(report.title[:80] if report.title else "Report")
            desc = html_module.escape(report.description[:150] if report.description else "")
            content = html_module.escape(report.content or "")
            
            report_cards += f'''
            <div class="report-card" id="report-{idx}" onclick="toggleReport('report-{idx}')">
                <div class="report-header">
                    <div>
                        <div class="report-title">{title}</div>
                        <div class="report-desc">{desc}</div>
                    </div>
                    <span class="report-date">{timestamp}</span>
                </div>
                <div class="report-content">{content}</div>
            </div>
            '''
        
        reports_content = report_cards
    else:
        reports_content = '''
        <div class="empty-state">
            <div class="empty-state-title">No reports yet</div>
            <div class="empty-state-sub">Reports will appear here as HossAgent completes work for you.</div>
        </div>
        '''
    
    with open("templates/customer_portal.html", "r") as f:
        template = f.read()
    
    html = template.format(
        payment_message=payment_banner,
        plan_name=plan_name,
        status_class=status_class,
        status_label=status_label,
        autopilot_class=autopilot_class,
        autopilot_label=autopilot_label,
        billing_info=billing_info,
        account_cta=account_cta,
        opportunities_count=total_opportunities,
        opportunities_content=opportunities_content,
        reports_count=len(reports),
        reports_content=reports_content
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
        - enrichment_pending: Count of lead events pending enrichment
        - enrichment_complete_today: Count of lead events enriched today
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
    
    enrichment_pending = session.exec(
        select(func.count()).select_from(LeadEvent).where(
            (LeadEvent.enrichment_status == None) | 
            (LeadEvent.enrichment_status.in_(["UNENRICHED", "ENRICHING"]))
        )
    ).one()
    
    enrichment_complete_today = session.exec(
        select(func.count()).select_from(LeadEvent).where(
            (LeadEvent.enrichment_status.in_(["ENRICHED", "OUTBOUND_READY"])) &
            (LeadEvent.enriched_at != None) &
            (LeadEvent.enriched_at >= today_start)
        )
    ).one()
    
    return {
        "signals_today": signals_today,
        "lead_events_today": lead_events_today,
        "outbound_sent_today": outbound_sent_today,
        "reports_delivered_today": reports_delivered_today,
        "errors_failed": errors_failed,
        "enrichment_pending": enrichment_pending,
        "enrichment_complete_today": enrichment_complete_today
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
            "enrichment_status": e.enrichment_status or "UNENRICHED",
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


# ============================================================================
# SIGNALNET ADMIN API ENDPOINTS
# ============================================================================


@app.get("/api/admin/signalnet/status")
def get_signalnet_status(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Get comprehensive SignalNet status including mode, sources, and recent signals.
    
    Returns:
    - mode: Current SIGNAL_MODE (PRODUCTION/SANDBOX/OFF)
    - lead_geography: Configured geography filter
    - lead_niche: Configured niche filter
    - leadevent_threshold: Score threshold for creating LeadEvents
    - registry: Status of all registered signal sources
    - total_signals: Total signals in database
    - last_pipeline_run: Most recent signal timestamp (approximation)
    - recent_signals: Last 20 signals with details
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    status = get_signal_status()
    
    total_signals = session.exec(select(func.count(Signal.id))).one()
    
    last_signal = session.exec(
        select(Signal).order_by(Signal.created_at.desc()).limit(1)
    ).first()
    last_pipeline_run = last_signal.created_at.isoformat() if last_signal else None
    
    recent_signals = session.exec(
        select(Signal).order_by(Signal.created_at.desc()).limit(20)
    ).all()
    
    signals_with_events = []
    for sig in recent_signals:
        lead_event = session.exec(
            select(LeadEvent).where(LeadEvent.signal_id == sig.id)
        ).first()
        
        company_name = None
        if sig.company_id:
            customer = session.exec(
                select(Customer).where(Customer.id == sig.company_id)
            ).first()
            if customer:
                company_name = customer.company
        
        category = lead_event.category if lead_event else None
        score = lead_event.urgency_score if lead_event else None
        
        signals_with_events.append({
            "id": sig.id,
            "source_type": sig.source_type,
            "context_summary": sig.context_summary,
            "geography": sig.geography,
            "company_id": sig.company_id,
            "company_name": company_name,
            "created_at": sig.created_at.isoformat() if sig.created_at else None,
            "has_lead_event": lead_event is not None,
            "lead_event_id": lead_event.id if lead_event else None,
            "category": category,
            "score": score,
            "status": getattr(sig, 'status', 'ACTIVE'),
            "noisy_pattern": getattr(sig, 'noisy_pattern', False),
        })
    
    return {
        "mode": status["mode"],
        "lead_geography": status["lead_geography"],
        "lead_niche": status["lead_niche"],
        "leadevent_threshold": status["leadevent_threshold"],
        "registry": status["registry"],
        "total_signals": total_signals,
        "last_pipeline_run": last_pipeline_run,
        "recent_signals": signals_with_events,
    }


@app.post("/api/admin/signalnet/mode")
def change_signalnet_mode(
    request: Request,
    new_mode: str = Query(..., description="New mode: PRODUCTION, SANDBOX, or OFF"),
    session: Session = Depends(get_session)
):
    """
    Change SIGNAL_MODE.
    
    NOTE: This changes the environment variable at runtime but won't persist after restart.
    For permanent changes, update the SIGNAL_MODE environment variable in Replit Secrets.
    
    Valid modes:
    - PRODUCTION: Run real sources, create LeadEvents for high-scoring signals
    - SANDBOX: Run sources and score signals, but don't create LeadEvents
    - OFF: Skip signal ingestion entirely
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    new_mode_upper = new_mode.upper()
    if new_mode_upper not in ("PRODUCTION", "SANDBOX", "OFF"):
        raise HTTPException(status_code=400, detail="Invalid mode. Use PRODUCTION, SANDBOX, or OFF")
    
    import os
    old_mode = os.environ.get("SIGNAL_MODE", "SANDBOX")
    os.environ["SIGNAL_MODE"] = new_mode_upper
    
    import signal_sources as ss
    ss.SIGNAL_MODE = new_mode_upper
    
    print(f"[SIGNALNET][ADMIN] Mode changed: {old_mode} -> {new_mode_upper}")
    
    return {
        "success": True,
        "old_mode": old_mode,
        "new_mode": new_mode_upper,
        "message": f"SIGNAL_MODE changed to {new_mode_upper}. Note: This is a runtime change. Update SIGNAL_MODE in Replit Secrets for persistence."
    }


@app.post("/api/admin/signalnet/run")
def run_signalnet_pipeline(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Trigger immediate SignalNet pipeline run.
    
    Runs all eligible signal sources through the pipeline:
    1. Fetch raw signals from each source
    2. Parse into standardized format
    3. Score each signal
    4. Persist to database
    5. Create LeadEvents for high-scoring signals (PRODUCTION mode only)
    
    Returns pipeline execution results.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        result = run_signal_pipeline(session)
        
        return {
            "success": True,
            "message": f"Pipeline complete: {result['signals_persisted']} signals, {result['events_created']} events",
            "result": result
        }
    except Exception as e:
        print(f"[SIGNALNET][ADMIN] Pipeline error: {e}")
        return {
            "success": False,
            "message": f"Pipeline error: {str(e)}",
            "result": None
        }


@app.post("/api/admin/signalnet/source/{source_name}/toggle")
def toggle_signalnet_source(
    source_name: str,
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Toggle a signal source enabled/disabled status.
    
    NOTE: Source enabled status is determined by the source's `enabled` property,
    which typically checks API keys and SIGNAL_MODE. This endpoint provides info
    about the source but cannot directly toggle most sources.
    
    For sources like weather_openweather that require API keys, 
    set/unset the environment variable to enable/disable.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    registry = get_registry()
    source = registry.get_source(source_name)
    
    if not source:
        raise HTTPException(status_code=404, detail=f"Source '{source_name}' not found")
    
    return {
        "source_name": source_name,
        "source_type": source.source_type,
        "enabled": source.enabled,
        "is_eligible": source.is_eligible(),
        "last_run": source.last_run.isoformat() if source.last_run else None,
        "last_error": source.last_error,
        "items_last_run": source.items_last_run,
        "cooldown_seconds": source.cooldown_seconds,
        "message": "Source status retrieved. To enable/disable, configure the required environment variables (API keys, SIGNAL_MODE)."
    }


@app.post("/api/admin/signalnet/clear-old")
def clear_old_signals(
    request: Request,
    days: int = Query(default=7, description="Delete signals older than this many days"),
    session: Session = Depends(get_session)
):
    """
    Clear signals older than specified days.
    
    This helps manage database size by removing old signal data.
    LeadEvents are NOT deleted - only the raw Signal records.
    
    Default: 7 days
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    old_signals = session.exec(
        select(Signal).where(Signal.created_at < cutoff_date)
    ).all()
    
    count = len(old_signals)
    
    for sig in old_signals:
        session.delete(sig)
    
    session.commit()
    
    print(f"[SIGNALNET][ADMIN] Cleared {count} signals older than {days} days")
    
    return {
        "success": True,
        "deleted_count": count,
        "cutoff_date": cutoff_date.isoformat(),
        "message": f"Deleted {count} signals older than {days} days"
    }


@app.post("/api/admin/signalnet/signal/{signal_id}/promote")
def promote_signal_to_event(
    signal_id: int,
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Manually promote a signal to a LeadEvent.
    
    Creates a new LeadEvent from the signal's context and marks the signal as PROMOTED.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    signal = session.exec(
        select(Signal).where(Signal.id == signal_id)
    ).first()
    
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    
    existing_event = session.exec(
        select(LeadEvent).where(LeadEvent.signal_id == signal_id)
    ).first()
    
    if existing_event:
        return {
            "success": False,
            "error": f"Signal already has LeadEvent #{existing_event.id}",
            "lead_event_id": existing_event.id
        }
    
    try:
        payload = json.loads(signal.raw_payload) if signal.raw_payload else {}
    except:
        payload = {}
    
    category = payload.get("category", "OPPORTUNITY")
    score = payload.get("score", 65)
    
    lead_event = LeadEvent(
        company_id=signal.company_id,
        lead_id=signal.lead_id,
        signal_id=signal.id,
        summary=signal.context_summary or f"Manual promotion from signal #{signal.id}",
        category=category,
        urgency_score=score,
        status="NEW",
        recommended_action="Manual review - promoted by admin"
    )
    
    session.add(lead_event)
    
    signal.status = "PROMOTED"
    session.add(signal)
    
    session.commit()
    session.refresh(lead_event)
    
    print(f"[SIGNALNET][ADMIN] Promoted signal {signal_id} to LeadEvent {lead_event.id}")
    
    return {
        "success": True,
        "message": f"Signal promoted to LeadEvent #{lead_event.id}",
        "lead_event_id": lead_event.id,
        "signal_id": signal_id
    }


@app.post("/api/admin/signalnet/signal/{signal_id}/discard")
def discard_signal(
    signal_id: int,
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Mark a signal as discarded/ignored.
    
    The signal will be marked with status=DISCARDED and hidden from the active stream.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    signal = session.exec(
        select(Signal).where(Signal.id == signal_id)
    ).first()
    
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    
    signal.status = "DISCARDED"
    session.add(signal)
    session.commit()
    
    print(f"[SIGNALNET][ADMIN] Discarded signal {signal_id}")
    
    return {
        "success": True,
        "message": f"Signal #{signal_id} discarded",
        "signal_id": signal_id
    }


@app.post("/api/admin/signalnet/signal/{signal_id}/flag-noisy")
def flag_signal_noisy(
    signal_id: int,
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Flag a signal's source pattern as noisy.
    
    Marks the signal with noisy_pattern=True. Future signals from similar
    source patterns may be suppressed or given lower priority.
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    signal = session.exec(
        select(Signal).where(Signal.id == signal_id)
    ).first()
    
    if not signal:
        raise HTTPException(status_code=404, detail=f"Signal {signal_id} not found")
    
    signal.noisy_pattern = True
    session.add(signal)
    session.commit()
    
    print(f"[SIGNALNET][ADMIN] Flagged signal {signal_id} source pattern as noisy (source_type: {signal.source_type})")
    
    return {
        "success": True,
        "message": f"Signal #{signal_id} flagged as noisy pattern. Source type: {signal.source_type}",
        "signal_id": signal_id,
        "source_type": signal.source_type
    }


# ============================================================================
# CONVERSATION ENGINE ADMIN API ENDPOINTS
# ============================================================================


@app.get("/api/admin/conversations/summary")
def get_conversations_summary(
    request: Request,
    session: Session = Depends(get_session)
):
    """
    Get conversation engine summary for admin console.
    
    Returns:
    - total_threads: Total conversation threads across all customers
    - threads_by_status: Count of threads by status (OPEN, HUMAN_OWNED, AUTO, CLOSED)
    - pending_drafts: Number of AI draft messages awaiting approval
    - recent_threads: Last 20 threads with key details
    - metrics: Aggregate conversation metrics
    """
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    total_threads = session.exec(select(func.count(Thread.id))).one()
    
    threads_by_status = {}
    for status in ["OPEN", "HUMAN_OWNED", "AUTO", "CLOSED"]:
        count = session.exec(
            select(func.count(Thread.id)).where(Thread.status == status)
        ).one()
        threads_by_status[status] = count
    
    pending_drafts = session.exec(
        select(func.count(Message.id)).where(Message.status == MESSAGE_STATUS_DRAFT)
    ).one()
    
    recent_threads = session.exec(
        select(Thread).order_by(Thread.updated_at.desc()).limit(20)
    ).all()
    
    threads_list = []
    for thread in recent_threads:
        customer = session.exec(
            select(Customer).where(Customer.id == thread.customer_id)
        ).first()
        
        threads_list.append({
            "id": thread.id,
            "lead_email": thread.lead_email,
            "lead_name": thread.lead_name,
            "lead_company": thread.lead_company,
            "customer_id": thread.customer_id,
            "customer_company": customer.company if customer else None,
            "status": thread.status,
            "message_count": thread.message_count,
            "last_direction": thread.last_direction,
            "last_summary": thread.last_summary[:80] if thread.last_summary else None,
            "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
            "created_at": thread.created_at.isoformat() if thread.created_at else None
        })
    
    total_messages = session.exec(select(func.count(Message.id))).one()
    inbound_count = session.exec(
        select(func.count(Message.id)).where(Message.direction == "INBOUND")
    ).one()
    outbound_count = session.exec(
        select(func.count(Message.id)).where(Message.direction == "OUTBOUND")
    ).one()
    
    return {
        "total_threads": total_threads,
        "threads_by_status": threads_by_status,
        "pending_drafts": pending_drafts,
        "recent_threads": threads_list,
        "total_messages": total_messages,
        "inbound_count": inbound_count,
        "outbound_count": outbound_count
    }


@app.get("/api/admin/conversations/drafts")
def get_admin_drafts(
    request: Request,
    session: Session = Depends(get_session)
):
    """Get all pending draft messages for admin review."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    drafts = session.exec(
        select(Message).where(Message.status == MESSAGE_STATUS_DRAFT)
        .order_by(Message.created_at.desc()).limit(50)
    ).all()
    
    drafts_list = []
    for msg in drafts:
        customer = session.exec(
            select(Customer).where(Customer.id == msg.customer_id)
        ).first()
        
        guardrails = []
        if msg.guardrail_flags:
            try:
                guardrails = json.loads(msg.guardrail_flags)
            except:
                pass
        
        drafts_list.append({
            "id": msg.id,
            "thread_id": msg.thread_id,
            "to_email": msg.to_email,
            "subject": msg.subject,
            "body_preview": msg.body_text[:200] if msg.body_text else None,
            "customer_id": msg.customer_id,
            "customer_company": customer.company if customer else None,
            "guardrails": guardrails,
            "created_at": msg.created_at.isoformat() if msg.created_at else None
        })
    
    return {"drafts": drafts_list, "count": len(drafts_list)}


# ============================================================================
# ADMIN CONSOLE - CONSOLIDATED API ENDPOINTS
# ============================================================================


@app.get("/api/admin/pipeline")
def get_admin_pipeline(request: Request, session: Session = Depends(get_session)):
    """Get unified pipeline of all opportunities (Signal → LeadEvent → Email)."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    pipeline = []
    events = session.exec(
        select(LeadEvent).order_by(LeadEvent.created_at.desc()).limit(100)
    ).all()
    
    for event in events:
        customer = session.exec(select(Customer).where(Customer.id == event.company_id)).first()
        outbound = session.exec(
            select(PendingOutbound).where(PendingOutbound.lead_event_id == event.id)
            .order_by(PendingOutbound.created_at.desc()).limit(1)
        ).first()
        
        stage = "Signal"
        if event.enriched_at:
            stage = "Enriched"
        if outbound:
            stage = "Email Generated"
        if outbound and outbound.status == "SENT":
            stage = "Email Sent"
        
        result = "Sent" if (outbound and outbound.status == "SENT") else "Skipped" if (outbound and outbound.status == "SKIPPED") else "Suppressed" if event.do_not_contact else "Pending"
        
        pipeline.append({
            "timestamp": event.created_at.isoformat(),
            "customer": customer.company if customer else "Unknown",
            "lead_company": event.lead_company or "Unknown",
            "signal_type": "News/Social" if event.signal_source else "Manual",
            "stage": stage,
            "result": result
        })
    
    return pipeline


@app.get("/api/admin/activity-log")
def get_admin_activity_log(request: Request, session: Session = Depends(get_session)):
    """Get chronological activity log of all events."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    activities = []
    
    signals = session.exec(
        select(Signal).order_by(Signal.created_at.desc()).limit(50)
    ).all()
    for sig in signals:
        activities.append({
            "timestamp": sig.created_at.isoformat(),
            "event": "Signal Detected",
            "customer": None,
            "details": f"{sig.source_type}: {sig.summary[:60] if sig.summary else 'N/A'}"
        })
    
    events = session.exec(
        select(LeadEvent).order_by(LeadEvent.created_at.desc()).limit(50)
    ).all()
    for evt in events:
        cust = session.exec(select(Customer).where(Customer.id == evt.company_id)).first()
        activities.append({
            "timestamp": evt.created_at.isoformat(),
            "event": "LeadEvent Created",
            "customer": cust.company if cust else None,
            "details": f"{evt.lead_company}: {evt.summary[:60] if evt.summary else 'N/A'}"
        })
    
    outbounds = session.exec(
        select(PendingOutbound).order_by(PendingOutbound.created_at.desc()).limit(50)
    ).all()
    for out in outbounds:
        cust = session.exec(select(Customer).where(Customer.id == out.customer_id)).first()
        activities.append({
            "timestamp": out.created_at.isoformat(),
            "event": f"Email {out.status}",
            "customer": cust.company if cust else None,
            "details": f"To: {out.to_email}"
        })
    
    activities.sort(key=lambda x: x["timestamp"], reverse=True)
    return activities[:100]


@app.get("/api/admin/customers-list")
def get_admin_customers_list(request: Request, session: Session = Depends(get_session)):
    """Get all customers with plan/usage info."""
    admin_token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not verify_admin_session(admin_token):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    customers = session.exec(select(Customer)).all()
    result = []
    
    for cust in customers:
        plan_status = get_customer_plan_status(cust)
        result.append({
            "company": cust.company,
            "contact_name": cust.contact_name,
            "plan": "Pro" if plan_status.is_paid else "Trial",
            "status": "Active" if plan_status.is_paid else ("Expired" if plan_status.is_expired else "Active"),
            "autopilot": cust.autopilot_enabled,
            "tasks_used": plan_status.tasks_used,
            "tasks_limit": plan_status.tasks_limit,
            "leads_used": plan_status.leads_used,
            "leads_limit": plan_status.leads_limit,
            "public_token": cust.public_token
        })
    
    return result


@app.get("/admin/logout")
def admin_logout():
    """Logout admin and clear cookie."""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)

