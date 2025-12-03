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
import secrets
from datetime import datetime
from sqlmodel import Session, select
from models import (
    Lead, Customer, Task, Invoice, LeadEvent, 
    BusinessProfile, PendingOutbound, Report,
    TRIAL_TASK_LIMIT, TRIAL_LEAD_LIMIT,
    OUTREACH_MODE_AUTO, OUTREACH_MODE_REVIEW,
    LEAD_STATUS_NEW, LEAD_STATUS_CONTACTED,
    NEXT_STEP_OWNER_AGENT, NEXT_STEP_OWNER_CUSTOMER
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
    create_pending_outbound
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
    1. Selects LeadEvents with status='new' ordered by urgency_score (highest first)
    2. Checks customer's outreach_mode and do_not_contact list
    3. Generates Miami-style contextual emails based on event.summary and event.recommended_action
    4. If AUTO mode: sends email immediately
    5. If REVIEW mode: creates PendingOutbound for customer approval
    6. Gets CC/Reply-To from BusinessProfile.primary_contact_email
    7. Updates event status to 'contacted' and stores the outbound_message
    
    Miami-Style Template Language:
    - Lead with the observed moment (the signal)
    - Tie to Miami context (local relevance, bilingual, hurricane season, etc.)
    - Offer clarity and next step
    
    Safe to call repeatedly - only processes events with status='new'.
    """
    max_events = get_max_emails_per_cycle()
    email_status = get_email_status()
    effective_mode = email_status["mode"]
    
    new_events = session.exec(
        select(LeadEvent)
        .where(LeadEvent.status == "new")
        .order_by(LeadEvent.urgency_score.desc())
        .limit(max_events)
    ).all()
    
    if not new_events:
        msg = "Event-Driven BizDev: No new lead events to process."
        print(f"[CYCLE] {msg}")
        return msg
    
    events_processed = 0
    events_contacted = 0
    events_failed = 0
    events_queued = 0
    events_blocked = 0
    contacted_summaries = []

    for event in new_events:
        lead = None
        customer = None
        contact_email = None
        contact_name = None
        company_name = None
        niche = "small business"
        
        business_profile = None
        outreach_mode = OUTREACH_MODE_AUTO
        do_not_contact_list = None
        cc_email = None
        reply_to = None
        
        if event.company_id:
            customer = session.exec(
                select(Customer).where(Customer.id == event.company_id)
            ).first()
            if customer:
                outreach_mode = customer.outreach_mode or OUTREACH_MODE_AUTO
                business_profile = get_business_profile(session, customer.id)
                if business_profile:
                    do_not_contact_list = business_profile.do_not_contact_list
                    if business_profile.primary_contact_email:
                        cc_email = business_profile.primary_contact_email
                        reply_to = business_profile.primary_contact_email
        
        if event.lead_id:
            lead = session.exec(
                select(Lead).where(Lead.id == event.lead_id)
            ).first()
            if lead:
                contact_email = lead.email
                contact_name = lead.name
                company_name = lead.company
                niche = lead.niche or niche
        
        if event.company_id and not lead:
            if customer:
                contact_email = customer.contact_email
                contact_name = customer.contact_name or customer.company
                company_name = customer.company
                niche = customer.niche or niche
        
        if not contact_email or not company_name:
            print(f"[EVENT-BIZDEV] Event {event.id}: No contact found, skipping")
            continue
        
        if check_do_not_contact(contact_email, do_not_contact_list):
            events_blocked += 1
            print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: BLOCKED (do_not_contact)")
            continue
        
        events_processed += 1
        
        subject, body = generate_miami_contextual_email(
            contact_name=contact_name or "there",
            company_name=company_name,
            niche=niche,
            event_summary=event.summary,
            recommended_action=event.recommended_action or "contextual outreach",
            category=event.category,
            urgency_score=event.urgency_score
        )
        
        if outreach_mode == OUTREACH_MODE_REVIEW and customer:
            create_pending_outbound(
                session=session,
                customer_id=customer.id,
                lead_id=event.lead_id,
                to_email=contact_email,
                to_name=contact_name,
                subject=subject,
                body=body,
                context_summary=f"Signal-triggered: {event.category} - {event.summary[:100]}",
                lead_event_id=event.id
            )
            event.outbound_message = body
            event.next_step = "Awaiting your review"
            event.next_step_owner = NEXT_STEP_OWNER_CUSTOMER
            events_queued += 1
            session.add(event)
            print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: QUEUED for review (urgency={event.urgency_score})")
            continue
        
        email_result = send_email(
            to_email=contact_email,
            subject=subject,
            body=body,
            lead_name=contact_name,
            company=company_name,
            cc_email=cc_email,
            reply_to=reply_to
        )
        
        event.outbound_message = body
        
        if email_result.actually_sent:
            event.status = LEAD_STATUS_CONTACTED
            event.last_contact_at = datetime.utcnow()
            event.last_contact_summary = f"Contextual email sent: {event.category}"
            event.next_step_owner = NEXT_STEP_OWNER_AGENT
            events_contacted += 1
            contacted_summaries.append(f"{company_name} ({event.category})")
            print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: CONTACTED (urgency={event.urgency_score})")
        elif email_result.result in ("dry_run", "fallback"):
            event.status = LEAD_STATUS_CONTACTED
            print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: DRY_RUN (mode={email_result.mode})")
        else:
            events_failed += 1
            print(f"[EVENT-BIZDEV] Event {event.id} for {company_name}: FAILED error=\"{email_result.error}\"")
        
        session.add(event)

    session.commit()
    
    summaries_str = ", ".join(contacted_summaries[:5]) if contacted_summaries else "None"
    if len(contacted_summaries) > 5:
        summaries_str += f" (+{len(contacted_summaries) - 5} more)"
    
    queued_info = f", Queued: {events_queued}" if events_queued > 0 else ""
    blocked_info = f", Blocked: {events_blocked}" if events_blocked > 0 else ""
    msg = f"Event-Driven BizDev: Processed {events_processed} events, contacted {events_contacted}. Failed: {events_failed}{queued_info}{blocked_info}. Mode: {effective_mode}. Companies: {summaries_str}"
    print(f"[CYCLE] {msg}")
    return msg


def generate_miami_contextual_email(
    contact_name: str,
    company_name: str,
    niche: str,
    event_summary: str,
    recommended_action: str,
    category: str,
    urgency_score: int
) -> tuple[str, str]:
    """
    Generate Miami-style contextual email based on signal event.
    
    Miami-Style Template Structure:
    1. Lead with the observed moment (the signal)
    2. Tie to Miami context (local market, bilingual advantage, weather, etc.)
    3. Offer clarity and a clear next step
    
    Returns: (subject, body) tuple
    """
    import os
    sender_name = os.environ.get("BIZDEV_SENDER_NAME", "HossAgent")
    
    category_intros = {
        "HURRICANE_SEASON": f"With hurricane season in full swing here in South Florida",
        "COMPETITOR_SHIFT": f"I noticed some movement in the local {niche} market",
        "GROWTH_SIGNAL": f"Saw some positive signals coming from {company_name}",
        "BILINGUAL_OPPORTUNITY": f"The Miami market rewards bilingual operations",
        "REPUTATION_CHANGE": f"Your online reputation is currency in Miami",
        "MIAMI_PRICE_MOVE": f"Pricing is shifting in the local {niche} space",
        "OPPORTUNITY": f"Something caught my attention about {company_name}"
    }
    
    category_closers = {
        "HURRICANE_SEASON": "Miami businesses that prepare early win when the storms come. Let me show you how we help.",
        "COMPETITOR_SHIFT": "Staying ahead of local competition is everything here. Happy to share what I'm seeing.",
        "GROWTH_SIGNAL": "Growth is good - but it needs infrastructure. That's where we come in.",
        "BILINGUAL_OPPORTUNITY": "The bilingual edge is real ROI in South Florida. Let's talk about capturing it.",
        "REPUTATION_CHANGE": "Your online presence shapes your pipeline. I can help you control the narrative.",
        "MIAMI_PRICE_MOVE": "Market moves create opportunities for those paying attention. Are you?",
        "OPPORTUNITY": "When I see moments like this, I reach out. Sometimes timing is everything."
    }
    
    intro = category_intros.get(category, category_intros["OPPORTUNITY"])
    closer = category_closers.get(category, category_closers["OPPORTUNITY"])
    
    urgency_flag = ""
    if urgency_score >= 75:
        urgency_flag = " [Time-Sensitive]"
    elif urgency_score >= 60:
        urgency_flag = ""
    
    subject = f"{company_name} - noticed something{urgency_flag}"
    
    body = f"""Hi {contact_name},

{intro}, I wanted to reach out.

Here's what I observed: {event_summary}

My recommendation: {recommended_action}

{closer}

Would a quick 15-minute call this week make sense? I'll come prepared with specifics.

- {sender_name}

P.S. I work with {niche} businesses across Miami-Dade and Broward. This isn't a mass email - I'm reaching out because the timing seems right."""

    return subject, body
