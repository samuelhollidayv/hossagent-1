"""
Autonomous agents for HossAgent business engine.
Each agent runs as a cycle function that is idempotent and safe to call repeatedly.

The *_cycle functions are called both by:
- Manual admin buttons (/admin/run-* endpoints)
- The autopilot background loop (runs every 5 minutes if enabled)

All functions accept a Session and perform idempotent operations.
"""
import asyncio
from datetime import datetime
from sqlmodel import Session, select
from models import Lead, Customer, Task, Invoice
from email_utils import (
    send_email,
    get_email_mode,
    get_max_emails_per_cycle,
    get_email_status,
    EmailMode
)
from bizdev_templates import (
    generate_email,
    log_template_generation,
    get_template_status
)
import random


async def run_bizdev_cycle(session: Session) -> str:
    """
    BizDev Cycle: Send outbound emails to NEW leads using template engine.
    
    Steps:
    1. Find leads with status='new' that haven't been contacted
    2. For each NEW lead, generate personalized email from template pack
    3. If email succeeds, mark lead as 'contacted'
    4. If email fails, mark as 'email_failed'
    5. If dry-run, keep as 'new'
    
    Throttling: Respects MAX_EMAILS_PER_CYCLE and MAX_EMAILS_PER_HOUR.
    Safe to call repeatedly - only emails leads with status='new'.
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
    emails_attempted = 0
    contacted_companies = []

    for lead in new_leads:
        if emails_attempted >= max_emails:
            print(f"[BIZDEV] Throttle limit reached ({max_emails} emails per cycle)")
            break
        
        generated = generate_email(
            first_name=lead.name,
            company_name=lead.company,
            niche=lead.niche,
            email=lead.email
        )
        
        log_template_generation(generated, lead.id, lead.email)

        emails_attempted += 1
        email_sent = send_email(
            to_email=lead.email,
            subject=generated.subject,
            body=generated.body,
            lead_name=lead.name,
            company=lead.company
        )
        
        if email_sent:
            lead.status = "contacted"
            lead.last_contacted_at = datetime.utcnow()
            emails_sent += 1
            contacted_companies.append(lead.company)
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: CONTACTED (template={generated.template_pack})")
        elif effective_mode == "DRY_RUN":
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: status=new (mode={effective_mode})")
        else:
            lead.status = "email_failed"
            emails_failed += 1
            print(f"[BIZDEV] Lead {lead.name} at {lead.company}: EMAIL_FAILED")

        session.add(lead)

    session.commit()
    
    companies_str = ", ".join(contacted_companies) if contacted_companies else "None"
    msg = f"BizDev: Contacted {emails_sent}/{emails_attempted} leads ({companies_str}). Failed: {emails_failed}. Mode: {effective_mode}, Template: {template_status['active_pack']}"
    print(f"[CYCLE] {msg}")
    return msg


async def run_onboarding_cycle(session: Session) -> str:
    """
    Onboarding Cycle: Convert a contacted/responded lead into a customer.
    Create 1-2 template tasks for the customer.
    Mark lead as qualified.
    
    Priority: 'responded' leads first, then 'contacted' leads.
    Idempotent: Skips leads already converted.
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
        plan="starter",
        billing_plan="starter",
        status="active",
        notes=f"Converted from lead: {lead.company}",
    )
    session.add(customer)
    session.flush()

    task_descriptions = [
        f"Initial market research for {lead.company}",
        f"Competitive landscape review for {lead.niche}",
    ]
    tasks_created = 0
    for desc in task_descriptions[:random.randint(1, 2)]:
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

    msg = f"Onboarding: Converted {lead.company} → Customer {customer.id}. Created {tasks_created} tasks."
    print(f"[CYCLE] {msg}")
    return msg


async def run_ops_cycle(session: Session) -> str:
    """
    Ops Cycle: Pick next pending task, mark running, simulate work, mark done.
    Calculates cost and profit.
    
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

    task.status = "running"
    session.add(task)
    session.commit()

    simulated_result = f"Research Summary: Analyzed '{task.description}' for {customer.company if customer else 'Unknown'}. Key findings: market opportunity identified, competitive positioning clear, actionable recommendations provided."
    cost_cents = random.randint(2, 8)
    profit_cents = max(0, task.reward_cents - cost_cents)

    task.status = "done"
    task.cost_cents = cost_cents
    task.profit_cents = profit_cents
    task.result_summary = simulated_result
    task.completed_at = datetime.utcnow()
    session.add(task)
    session.commit()

    msg = f"Ops: Completed task {task.id} ({customer.company if customer else 'Unknown'}). Cost: {cost_cents}¢, Profit: {profit_cents}¢"
    print(f"[CYCLE] {msg}")
    return msg


async def run_billing_cycle(session: Session) -> str:
    """
    Billing Cycle: Aggregate completed tasks per customer.
    Generate draft invoice records for uninvoiced work.
    
    Safe to call repeatedly: skips customers/tasks already invoiced.
    
    Hook for Stripe integration:
    - When invoice is created, call stripe_utils.create_stripe_checkout_session()
    - Store checkout URL in invoice.notes
    """
    statement = select(Customer).limit(100)
    customers = session.exec(statement).all()

    invoices_created = 0
    msg_parts = []

    for customer in customers:
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
                invoices_created += 1
                msg_parts.append(f"{customer.company}: ${total_reward/100:.2f}")

    session.commit()
    msg = f"Billing: Generated {invoices_created} invoices. " + ("; ".join(msg_parts) if msg_parts else "None.")
    print(f"[CYCLE] {msg}")
    return msg
