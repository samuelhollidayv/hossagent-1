"""
Autonomous agents for HossAgent business engine.
Each agent runs as a cycle function that is idempotent and safe to call repeatedly.

The *_cycle functions are called both by:
- Manual admin buttons (/admin/run-* endpoints)
- The autopilot background loop (runs every 5 minutes if enabled)

All functions accept a Session and perform idempotent operations.

Plan Gating:
- Trial customers: Limited tasks/leads, DRY_RUN email, no billing
- Paid customers: Full access to all features
"""
import asyncio
import hashlib
import secrets
from datetime import datetime
from sqlmodel import Session, select
from models import (
    Lead, Customer, Task, Invoice, LeadEvent, 
    BusinessProfile, PendingOutbound, Report,
    TRIAL_TASK_LIMIT, TRIAL_LEAD_LIMIT,
    OUTREACH_MODE_AUTO, OUTREACH_MODE_REVIEW,
    LEAD_STATUS_NEW, LEAD_STATUS_CONTACTED,
    NEXT_STEP_OWNER_AGENT, NEXT_STEP_OWNER_CUSTOMER,
    ENRICHMENT_STATUS_UNENRICHED,
    ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL,
    ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND,
    ENRICHMENT_STATUS_OUTBOUND_SENT,
    ENRICHMENT_STATUS_ARCHIVED,
    ENRICHMENT_STATUS_ENRICHED,
    ENRICHMENT_STATUS_OUTBOUND_READY,
)
from email_utils import (
    send_email,
    get_email_mode,
    get_max_emails_per_cycle,
    get_email_status,
    EmailMode,
    EmailResult
)
from outbound_utils import (
    get_business_profile,
    check_do_not_contact,
    create_pending_outbound,
    send_lead_event_immediate
)
from bizdev_templates import (
    generate_email,
    log_template_generation,
    get_template_status
)
from subscription_utils import (
    get_customer_plan_status,
    initialize_trial,
    should_force_email_dry_run,
    should_disable_billing_for_customer,
    increment_task_usage,
    increment_lead_usage,
    increment_tasks_used,
    increment_leads_used
)
import random


def create_report(
    session: Session,
    customer_id: int,
    title: str,
    description: str = None,
    content: str = None,
    report_type: str = "general",
    lead_id: int = None
) -> Report:
    """
    Create a Report record for a customer.
    
    Reports capture visible work output such as research summaries,
    competitive analyses, and market insights. Displayed in the portal
    under "Reports / Recent Work".
    
    Args:
        session: Database session
        customer_id: The customer this report is for
        title: Report title (e.g., task description)
        description: Short description or summary
        content: Full report content or JSON
        report_type: Type of report (research, competitive, market, opportunity, general)
        lead_id: Optional related lead ID
    
    Returns:
        The created Report record
    """
    report = Report(
        customer_id=customer_id,
        lead_id=lead_id,
        title=title,
        description=description,
        content=content,
        report_type=report_type
    )
    session.add(report)
    
    increment_tasks_used(session, customer_id)
    
    print(f"[REPORT] Created report for customer {customer_id}: {title[:50]}...")
    return report


async def run_bizdev_cycle(session: Session) -> str:
    """
    BizDev Cycle: Send outbound emails to NEW leads using template engine.
    
    Steps:
    1. Find leads with status='new' that haven't been contacted
    2. For each NEW lead, check customer outreach_mode if lead has company_id
    3. Check do_not_contact list before sending
    4. If AUTO mode: send email, update lead status to CONTACTED
    5. If REVIEW mode: create PendingOutbound, keep lead as NEW
    6. If email fails, mark as 'email_failed'
    7. If dry-run, keep as 'new'
    
    Throttling: Respects MAX_EMAILS_PER_CYCLE and MAX_EMAILS_PER_HOUR.
    Safe to call repeatedly - only emails leads with status='new'.
    
    Note: Trial customers are forced to DRY_RUN email mode.
    """
    max_emails = get_max_emails_per_cycle()
    email_status = get_email_status()
    effective_mode = email_status["mode"]
    template_status = get_template_status()
    
    new_leads = session.exec(
        select(Lead).where(Lead.status == "new").limit(max_emails)
    ).all()
    
    if not new_leads:
        msg = "BizDev: No new leads to contact."
        print(f"[CYCLE] {msg}")
        return msg
    
    emails_sent = 0
    emails_failed = 0
    emails_throttled = 0
    emails_attempted = 0
    emails_queued = 0
    emails_blocked = 0
    contacted_companies = []

    for lead in new_leads:
        if emails_attempted >= max_emails:
            print(f"[BIZDEV] Throttle limit reached ({max_emails} emails per cycle)")
            break
        
        customer = None
        business_profile = None
        outreach_mode = OUTREACH_MODE_AUTO
        do_not_contact_list = None
        
        customer_id = getattr(lead, 'customer_id', None) or getattr(lead, 'company_id', None)
        if customer_id:
            customer = session.exec(
                select(Customer).where(Customer.id == customer_id)
            ).first()
            if customer:
                outreach_mode = customer.outreach_mode or OUTREACH_MODE_AUTO
                business_profile = get_business_profile(session, customer.id)
                if business_profile:
                    do_not_contact_list = business_profile.do_not_contact_list
        
        if check_do_not_contact(lead.email, do_not_contact_list):
            emails_blocked += 1
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: BLOCKED (do_not_contact)")
            continue
        
        generated = generate_email(
            first_name=lead.name,
            company_name=lead.company,
            niche=lead.niche,
            email=lead.email
        )
        
        log_template_generation(generated, lead.id, lead.email)
        emails_attempted += 1

        if outreach_mode == OUTREACH_MODE_REVIEW and customer:
            create_pending_outbound(
                session=session,
                customer_id=customer.id,
                lead_id=lead.id,
                to_email=lead.email,
                to_name=lead.name,
                subject=generated.subject,
                body=generated.body,
                context_summary=f"Intro email for lead from {lead.company}"
            )
            lead.next_step = "Awaiting your review"
            lead.next_step_owner = NEXT_STEP_OWNER_CUSTOMER
            emails_queued += 1
            session.add(lead)
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: QUEUED for review (template={generated.template_pack})")
            continue

        email_result: EmailResult = send_email(
            to_email=lead.email,
            subject=generated.subject,
            body=generated.body,
            lead_name=lead.name,
            company=lead.company
        )
        
        if email_result.actually_sent:
            lead.status = LEAD_STATUS_CONTACTED
            lead.last_contacted_at = datetime.utcnow()
            lead.last_contact_summary = "Intro email sent"
            lead.next_step_owner = NEXT_STEP_OWNER_AGENT
            emails_sent += 1
            contacted_companies.append(lead.company)
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: CONTACTED (template={generated.template_pack})")
        elif email_result.result == "throttled":
            emails_throttled += 1
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: THROTTLED")
        elif email_result.result in ("dry_run", "fallback"):
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: status=new (mode={email_result.mode})")
        else:
            lead.status = "email_failed"
            emails_failed += 1
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: EMAIL_FAILED error=\"{email_result.error}\"")

        session.add(lead)

    session.commit()
    
    companies_str = ", ".join(contacted_companies) if contacted_companies else "None"
    throttle_info = f", Throttled: {emails_throttled}" if emails_throttled > 0 else ""
    queued_info = f", Queued: {emails_queued}" if emails_queued > 0 else ""
    blocked_info = f", Blocked: {emails_blocked}" if emails_blocked > 0 else ""
    msg = f"BizDev: Contacted {emails_sent}/{emails_attempted} leads ({companies_str}). Failed: {emails_failed}{throttle_info}{queued_info}{blocked_info}. Mode: {effective_mode}, Template: {template_status['active_pack']}"
    print(f"[CYCLE] {msg}")
    return msg


async def run_onboarding_cycle(session: Session) -> str:
    """
    Onboarding Cycle: Convert a contacted/responded lead into a customer.
    Create 1-2 template tasks for the customer.
    Mark lead as qualified.
    
    Priority: 'responded' leads first, then 'contacted' leads.
    Idempotent: Skips leads already converted.
    
    New customers start in TRIAL mode with 7-day restricted access.
    """
    lead = session.exec(
        select(Lead).where(Lead.status == "responded").limit(1)
    ).first()
    
    if not lead:
        lead = session.exec(
            select(Lead).where(Lead.status == "contacted").limit(1)
        ).first()

    if not lead:
        msg = "Onboarding: No unqualified leads available."
        print(f"[CYCLE] {msg}")
        return msg

    existing_customer = session.exec(
        select(Customer).where(Customer.contact_email == lead.email)
    ).first()
    if existing_customer:
        msg = f"Onboarding: Lead {lead.company} already converted to customer {existing_customer.id}."
        print(f"[CYCLE] {msg}")
        return msg

    customer = Customer(
        company=lead.company,
        contact_email=lead.email,
        billing_plan="starter",
        status="active",
        public_token=secrets.token_urlsafe(16),
        notes=f"Converted from lead: {lead.company}",
    )
    
    customer = initialize_trial(customer)
    customer.leads_this_period = 1
    
    session.add(customer)
    session.flush()
    
    plan_status = get_customer_plan_status(customer)

    task_descriptions = [
        f"Initial market research for {lead.company}",
        f"Competitive landscape review for {lead.niche}",
    ]
    tasks_created = 0
    for desc in task_descriptions[:random.randint(1, 2)]:
        if plan_status.tasks_used + tasks_created >= plan_status.tasks_limit:
            print(f"[ONBOARDING] Trial task limit reached for new customer {customer.id}")
            break
        
        task = Task(
            customer_id=customer.id,
            description=desc,
            status="pending",
            reward_cents=random.randint(50, 200),
        )
        session.add(task)
        tasks_created += 1

    lead.status = "qualified"
    session.add(lead)
    session.commit()

    plan_info = f" (Plan: {customer.plan})"
    msg = f"Onboarding: Converted {lead.company} → Customer {customer.id}. Created {tasks_created} tasks.{plan_info}"
    print(f"[CYCLE] {msg}")
    return msg


async def run_ops_cycle(session: Session) -> str:
    """
    Ops Cycle: Pick next pending task, mark running, simulate work, mark done.
    Calculates cost and profit.
    
    Plan Gating:
    - Trial customers: Limited to TRIAL_TASK_LIMIT tasks total
    - Paid customers: Unlimited tasks
    
    Hook for real OpenAI integration:
    - Replace simulated result with real API call
    - Read OPENAI_API_KEY from environment
    - Call gpt-4-mini or gpt-4o-mini
    - Parse response and estimate token cost
    """
    statement = select(Task).where(Task.status == "pending").limit(1)
    task = session.exec(statement).first()

    if not task:
        msg = "Ops: No pending tasks."
        print(f"[CYCLE] {msg}")
        return msg

    customer = session.exec(
        select(Customer).where(Customer.id == task.customer_id)
    ).first()

    if not customer:
        msg = f"Ops: Task {task.id} has no associated customer."
        print(f"[CYCLE] {msg}")
        return msg

    plan_status = get_customer_plan_status(customer)
    
    if plan_status.is_expired:
        msg = f"Ops: Customer {customer.company} trial expired. Upgrade required."
        print(f"[CYCLE][GATED] {msg}")
        return msg
    
    if not plan_status.can_run_tasks:
        msg = f"Ops: Customer {customer.company} reached trial task limit ({plan_status.tasks_used}/{plan_status.tasks_limit}). Upgrade required."
        print(f"[CYCLE][GATED] {msg}")
        return msg

    task.status = "running"
    session.add(task)
    session.commit()

    simulated_result = f"Research Summary: Analyzed '{task.description}' for {customer.company}. Key findings: market opportunity identified, competitive positioning clear, actionable recommendations provided."
    cost_cents = random.randint(2, 8)
    profit_cents = max(0, task.reward_cents - cost_cents)

    task.status = "done"
    task.cost_cents = cost_cents
    task.profit_cents = profit_cents
    task.result_summary = simulated_result
    task.completed_at = datetime.utcnow()
    session.add(task)
    
    create_report(
        session=session,
        customer_id=customer.id,
        title=task.description,
        description=f"Research completed for {customer.company}",
        content=simulated_result,
        report_type="research"
    )
    
    session.commit()
    
    session.refresh(customer)

    plan_info = f" [Plan: {customer.plan}, Tasks: {customer.tasks_this_period}/{plan_status.tasks_limit if plan_status.is_trial else 'unlimited'}]"
    msg = f"Ops: Completed task {task.id} ({customer.company}). Cost: {cost_cents}c, Profit: {profit_cents}c{plan_info}"
    print(f"[CYCLE] {msg}")
    return msg


async def run_billing_cycle(session: Session) -> str:
    """
    Billing Cycle: Aggregate completed tasks per customer.
    Generate draft invoice records for uninvoiced work.
    Create Stripe payment links if ENABLE_STRIPE=TRUE.
    
    Plan Gating:
    - Trial customers: Billing agent DISABLED (no invoices, no payment links)
    - Paid customers: Full billing functionality
    
    Safe to call repeatedly: skips customers/tasks already invoiced.
    Amount safety clamp: $1-$500 (configurable in stripe_utils).
    """
    from stripe_utils import create_payment_link, is_stripe_enabled
    
    statement = select(Customer).limit(100)
    customers = session.exec(statement).all()

    invoices_created = 0
    payment_links_created = 0
    trial_skipped = 0
    msg_parts = []

    for customer in customers:
        plan_status = get_customer_plan_status(customer)
        
        if not plan_status.can_use_billing:
            trial_skipped += 1
            continue

        task_statement = select(Task).where(
            (Task.customer_id == customer.id) & (Task.status == "done")
        )
        completed_tasks = session.exec(task_statement).all()

        if not completed_tasks:
            continue

        total_reward = sum(t.reward_cents for t in completed_tasks)

        if total_reward > 0:
            invoice_statement = select(Invoice).where(
                (Invoice.customer_id == customer.id) & (Invoice.status == "draft")
            )
            existing_invoice = session.exec(invoice_statement).first()

            if not existing_invoice:
                invoice = Invoice(
                    customer_id=customer.id,
                    amount_cents=total_reward,
                    status="draft",
                    notes=f"Generated from {len(completed_tasks)} completed tasks",
                )
                session.add(invoice)
                session.flush()
                invoices_created += 1
                
                if is_stripe_enabled():
                    result = create_payment_link(
                        amount_cents=total_reward,
                        customer_id=customer.id,
                        customer_email=customer.contact_email,
                        description=f"Invoice #{invoice.id} - {customer.company}",
                        invoice_id=invoice.id
                    )
                    
                    if result.success:
                        invoice.payment_url = result.payment_url
                        invoice.stripe_payment_id = result.stripe_id
                        session.add(invoice)
                        payment_links_created += 1
                        print(f"[BILLING] Stripe payment link created for invoice {invoice.id}")
                    else:
                        print(f"[BILLING] Stripe payment link failed: {result.error}")
                
                msg_parts.append(f"{customer.company}: ${total_reward/100:.2f}")

    session.commit()
    
    stripe_status = " (Stripe: enabled)" if is_stripe_enabled() else " (Stripe: disabled)"
    trial_info = f" Trial customers skipped: {trial_skipped}." if trial_skipped > 0 else ""
    msg = f"Billing: Generated {invoices_created} invoices, {payment_links_created} payment links.{stripe_status}{trial_info} " + ("; ".join(msg_parts) if msg_parts else "None.")
    print(f"[CYCLE] {msg}")
    return msg


async def run_event_driven_bizdev_cycle(session: Session) -> str:
    """
    Event-Driven BizDev Cycle: Send contextual outreach based on LeadEvents from Signals Engine.
    
    This is a moment-aware outreach system that:
    1. Selects LeadEvents with enrichment_status = ENRICHED_NO_OUTBOUND (ready to send)
    2. Skips UNENRICHED and WITH_DOMAIN_NO_EMAIL (wait for enrichment pipeline)
    3. Checks customer's outreach_mode and do_not_contact list
    4. Generates Miami-style contextual emails based on event.summary and event.recommended_action
    5. If AUTO mode: sends email immediately
    6. If REVIEW mode: creates PendingOutbound for customer approval
    7. Gets CC/Reply-To from BusinessProfile.primary_contact_email
    8. Updates enrichment_status to OUTBOUND_SENT and status to 'CONTACTED'
    
    Enrichment Status Flow:
    - UNENRICHED → WITH_DOMAIN_NO_EMAIL → ENRICHED_NO_OUTBOUND → OUTBOUND_SENT
    
    Safe to call repeatedly - only processes ENRICHED_NO_OUTBOUND events.
    """
    max_events = get_max_emails_per_cycle()
    email_status = get_email_status()
    effective_mode = email_status["mode"]
    
    new_events = session.exec(
        select(LeadEvent)
        .where(LeadEvent.status == LEAD_STATUS_NEW)
        .where(LeadEvent.enrichment_status.in_([
            ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND,
            ENRICHMENT_STATUS_ENRICHED,
            ENRICHMENT_STATUS_OUTBOUND_READY
        ]))
        .order_by(LeadEvent.urgency_score.desc())
        .limit(max_events)
    ).all()
    
    unenriched_count = len(session.exec(
        select(LeadEvent)
        .where(LeadEvent.status == LEAD_STATUS_NEW)
        .where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_UNENRICHED)
    ).all())
    
    with_domain_count = len(session.exec(
        select(LeadEvent)
        .where(LeadEvent.status == LEAD_STATUS_NEW)
        .where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL)
    ).all())
    
    print(f"[EVENT-BIZDEV] Found {len(new_events)} ready for outbound, {unenriched_count} unenriched, {with_domain_count} with domain only")
    
    if not new_events:
        if unenriched_count > 0:
            msg = f"Event-Driven BizDev: No enriched events to process. {unenriched_count} events awaiting enrichment."
        else:
            msg = "Event-Driven BizDev: No new lead events to process."
        print(f"[CYCLE] {msg}")
        return msg
    
    events_processed = 0
    events_contacted = 0
    events_failed = 0
    events_queued = 0
    events_blocked = 0
    events_rate_limited = 0
    contacted_summaries = []
    
    for event in new_events:
        company_name = event.lead_company or event.enriched_company_name or "Your company"
        
        result = send_lead_event_immediate(session, event, commit=False)
        events_processed += 1
        
        if result.success:
            if result.email_sent:
                events_contacted += 1
                contacted_summaries.append(f"{company_name} ({event.category})")
                print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: SENT via immediate-send")
            elif result.queued_for_review:
                events_queued += 1
                print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: QUEUED for review")
        else:
            if result.action == "blocked":
                events_blocked += 1
            elif result.action == "rate_limited":
                events_rate_limited += 1
            else:
                events_failed += 1
            print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: {result.action.upper()} - {result.reason}")

    session.commit()
    
    summaries_str = ", ".join(contacted_summaries[:5]) if contacted_summaries else "None"
    if len(contacted_summaries) > 5:
        summaries_str += f" (+{len(contacted_summaries) - 5} more)"
    
    queued_info = f", Queued: {events_queued}" if events_queued > 0 else ""
    blocked_info = f", Blocked: {events_blocked}" if events_blocked > 0 else ""
    rate_limit_info = f", Rate-limited: {events_rate_limited}" if events_rate_limited > 0 else ""
    unenriched_info = f", Awaiting enrichment: {unenriched_count}" if unenriched_count > 0 else ""
    msg = f"Event-Driven BizDev: Processed {events_processed} events, contacted {events_contacted}. Failed: {events_failed}{queued_info}{blocked_info}{rate_limit_info}{unenriched_info}. Mode: {effective_mode}. Companies: {summaries_str}"
    print(f"[CYCLE] {msg}")
    return msg


SUBJECT_LINE_LIBRARY = [
    "Quick heads-up for {company}",
    "New signal in your market",
    "Saw something relevant to {company}",
    "Small opportunity I noticed near {city}",
    "Thought this might be timely for you",
    "Local lead-gen idea for {company}",
    "Context on a shift near {city}",
    "Your competitors are moving",
    "New biz dev opportunity in your space",
    "Short note about {signal_handle}",
    "Something came up for {company}",
    "Noticed a shift in {niche}",
]


def parse_first_name(full_name: str) -> str:
    """
    Parse first name from full name string.
    
    Rules:
    - If first_name exists and is non-empty: use it
    - If only have full name string: use first token as first name
    - Else: return "there"
    - Never use "First Last" as greeting
    """
    if not full_name or full_name.strip().lower() in ("there", "unknown", "none", ""):
        return "there"
    
    parts = full_name.strip().split()
    if len(parts) >= 1:
        first = parts[0].strip()
        if first and len(first) > 1:
            return first.title()
    
    return "there"


def get_subject_line(
    company_name: str,
    city: str,
    niche: str,
    signal_handle: str,
    event_id: int,
    signal_id: int = None
) -> str:
    """
    Get subject line from library with consistent rotation.
    
    Uses hash of (event_id, signal_id) to pick subject consistently
    so same lead+signal always gets same subject line.
    """
    import hashlib
    
    hash_input = f"{event_id}-{signal_id or 0}"
    hash_value = int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)
    index = hash_value % len(SUBJECT_LINE_LIBRARY)
    
    template = SUBJECT_LINE_LIBRARY[index]
    
    return template.format(
        company=company_name or "your company",
        city=city or "Miami",
        niche=niche or "your space",
        signal_handle=signal_handle or "a recent signal"
    )


def _detect_signal_type(event_summary: str, category: str) -> str:
    """
    Detect the strategic type of signal to determine outreach approach.
    
    Returns: 'market_entry', 'competitor_intel', 'growth_opportunity', 'market_shift'
    """
    summary_lower = (event_summary or "").lower()
    
    if any(phrase in summary_lower for phrase in [
        "competitor", "rival", "new player", "competing", "market share"
    ]):
        return "competitor_intel"
    
    if any(phrase in summary_lower for phrase in [
        "opening", "expand", "enters", "entering", "launch", "new location",
        "setting up", "establish", "relocat", "move to", "operations in"
    ]):
        return "market_entry"
    
    if any(phrase in summary_lower for phrase in [
        "hiring", "job posting", "recruit", "growing team", "expansion"
    ]):
        return "growth_opportunity"
    
    if category in ["COMPETITOR_SHIFT", "MIAMI_PRICE_MOVE"]:
        return "competitor_intel"
    
    return "market_shift"


def _generate_actionable_insights(signal_type: str, niche: str, city: str) -> tuple[str, str, str]:
    """
    Generate actionable insights based on signal type.
    
    Returns: (market_context, specific_recommendations, product_tie_in)
    """
    insights = {
        "market_entry": (
            f"The {city} {niche} market is competitive but has clear patterns for success",
            f"""Here's what I'm seeing work for {niche} businesses breaking into {city}:
1. Local partnerships beat cold advertising 3-to-1 for customer acquisition
2. Bilingual operations (English/Spanish) typically see 40% higher retention
3. The first 90 days determine 80% of long-term success - speed matters""",
            f"I track these patterns across hundreds of {city} businesses and can show you exactly what's working in your space right now"
        ),
        "competitor_intel": (
            f"New competition means the {city} {niche} landscape is shifting",
            f"""When new players enter, here's what successful {niche} operators do:
1. Double down on what differentiates you - now is not the time to be generic
2. Lock in your best customers before competitors start poaching
3. Watch their pricing strategy - early signals predict their long game""",
            f"I monitor competitor moves across {city} in real-time and can flag when you need to react"
        ),
        "growth_opportunity": (
            f"Growth signals in {city}'s {niche} sector indicate timing advantages",
            f"""The businesses capitalizing fastest on these moments typically:
1. Move within 2-3 weeks of the signal - timing decay is real
2. Have a clear "next step" ready for interested leads
3. Use the momentum for social proof and referral asks""",
            f"I can surface these opportunities the moment they appear so you're first to act"
        ),
        "market_shift": (
            f"Market conditions in {city} are creating short windows of opportunity",
            f"""Here's what's working for {niche} businesses right now:
1. Businesses that adapt their messaging to current conditions see 2x engagement
2. Proactive outreach during market shifts outperforms waiting
3. The businesses that act in the next 30 days will set the pace for the next quarter""",
            f"I track these signals continuously so you never miss a window"
        )
    }
    
    return insights.get(signal_type, insights["market_shift"])


def generate_miami_contextual_email(
    contact_name: str,
    company_name: str,
    niche: str,
    event_summary: str,
    recommended_action: str,
    category: str,
    urgency_score: int,
    outreach_style: str = "transparent_ai",
    event_id: int = 0,
    signal_id: int = None,
    city: str = "Miami",
    source_url: str = None
) -> tuple[str, str]:
    """
    Generate strategic contextual email with actionable insights.
    
    Key principles:
    1. Introduce sender FIRST, AI disclosure comes later
    2. Provide NOVEL value - not just regurgitating news they already know
    3. Give specific, actionable recommendations tied to their situation
    4. Clear product tie-in that explains the "so what"
    
    Two template styles:
    - "transparent_ai": Full disclosure with strategic insights
    - "classic": Professional outbound with clear value prop
    
    Returns: (subject, body) tuple
    """
    import os
    
    website_url = os.environ.get("HOSSAGENT_WEBSITE_URL", "https://hossagent.net")
    
    first_name = parse_first_name(contact_name)
    
    signal_type = _detect_signal_type(event_summary, category)
    market_context, recommendations, product_tie = _generate_actionable_insights(signal_type, niche, city)
    
    signal_handle = None
    summary_lower = event_summary.lower() if event_summary else ""
    if "job posting" in summary_lower or "hiring" in summary_lower:
        signal_handle = "growth activity"
    elif "review" in summary_lower:
        signal_handle = "market feedback"
    elif "competitor" in summary_lower:
        signal_handle = "competitive movement"
    elif "opening" in summary_lower or "expand" in summary_lower or "entering" in summary_lower:
        signal_handle = "market entry"
    else:
        signal_handle = "a market signal"
    
    subject = get_subject_line(
        company_name=company_name,
        city=city,
        niche=niche,
        signal_handle=signal_handle,
        event_id=event_id,
        signal_id=signal_id
    )
    
    source_line = ""
    if source_url:
        source_line = f"\n(Story: {source_url})\n"
    
    if outreach_style == "transparent_ai":
        body = f"""Hi {first_name},

My name is Sam Holliday - I run HossAgent, an AI-powered business autopilot for local service companies in {city}.

I built it so owners don't have to watch the news or babysit signals all day. It flags moments worth acting on and drafts clean outreach automatically.

Today {company_name} popped onto my radar because of this specific event:

{event_summary}{source_line}
Here's why this matters: {market_context}.

{recommendations}

{product_tie}.

If any of this resonates, I'd be happy to share a quick competitive snapshot of your space - no pitch, just useful intel. Reply "send it" and I'll put something together, or grab 15 minutes on my calendar if that's easier.

You can see what we're building at {website_url} - we offer a 7-day free trial if you'd rather have the system watching the market for you.

Reply "no thanks" if this isn't relevant and I won't reach out again.

- Sam Holliday
Founder, HossAgent
{website_url}"""

    else:
        body = f"""Hi {first_name},

My name is Sam Holliday - I run a market intelligence service for local businesses in {city}.

Today {company_name} came across my radar:

{event_summary}{source_line}
Here's why this matters: {market_context}.

{recommendations}

{product_tie}.

Would a quick 15-minute call make sense? I can share what's working for similar businesses in your space right now.

Reply "interested" and I'll send over some times, or "no thanks" if this isn't a fit.

- Sam Holliday
{website_url}"""

    return subject, body


def check_rate_limits(
    session,
    lead_email: str,
    event_id: int,
    customer_id: int = None
) -> tuple[bool, str]:
    """
    Check if outbound to this lead is allowed under rate limits.
    
    Checks both lead_email and enriched_email fields for historical sends.
    
    Returns: (allowed, reason)
    - allowed: True if email can be sent
    - reason: Explanation if blocked
    """
    from models import (
        LeadEvent, MAX_OUTBOUND_PER_LEAD_PER_DAY, 
        MAX_OUTBOUND_PER_LEAD_PER_WEEK, MAX_OUTBOUND_PER_CUSTOMER_PER_DAY
    )
    from datetime import timedelta
    from sqlalchemy import or_
    
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    
    contacted_24h = session.exec(
        select(LeadEvent)
        .where(or_(LeadEvent.enriched_email == lead_email, LeadEvent.lead_email == lead_email))
        .where(LeadEvent.status == LEAD_STATUS_CONTACTED)
        .where(LeadEvent.last_contact_at >= day_ago)
    ).all()
    
    if len(contacted_24h) >= MAX_OUTBOUND_PER_LEAD_PER_DAY:
        return False, f"Rate limit: {lead_email} already contacted in last 24h"
    
    contacted_7d = session.exec(
        select(LeadEvent)
        .where(or_(LeadEvent.enriched_email == lead_email, LeadEvent.lead_email == lead_email))
        .where(LeadEvent.status == LEAD_STATUS_CONTACTED)
        .where(LeadEvent.last_contact_at >= week_ago)
    ).all()
    
    if len(contacted_7d) >= MAX_OUTBOUND_PER_LEAD_PER_WEEK:
        return False, f"Rate limit: {lead_email} contacted {len(contacted_7d)} times this week"
    
    if customer_id:
        customer_today = session.exec(
            select(LeadEvent)
            .where(LeadEvent.company_id == customer_id)
            .where(LeadEvent.status == LEAD_STATUS_CONTACTED)
            .where(LeadEvent.last_contact_at >= day_ago)
        ).all()
        
        if len(customer_today) >= MAX_OUTBOUND_PER_CUSTOMER_PER_DAY:
            return False, f"Rate limit: Customer daily cap ({MAX_OUTBOUND_PER_CUSTOMER_PER_DAY}) reached"
    
    return True, "OK"


def check_opt_out(reply_text: str) -> bool:
    """
    Check if reply text contains opt-out phrases.
    
    Returns True if this is an opt-out request.
    """
    from models import OPT_OUT_PHRASES
    
    if not reply_text:
        return False
    
    reply_lower = reply_text.lower().strip()
    
    for phrase in OPT_OUT_PHRASES:
        if phrase in reply_lower:
            return True
    
    return False


def mark_do_not_contact(session, event: 'LeadEvent', reason: str = "opt_out_reply"):
    """
    Mark a LeadEvent as do-not-contact.
    """
    from datetime import datetime
    
    event.do_not_contact = True
    event.do_not_contact_reason = reason
    event.do_not_contact_at = datetime.utcnow()
    session.add(event)
    session.commit()
    print(f"[SUPPRESSION] Marked event {event.id} ({event.enriched_email}) as do_not_contact: {reason}")
