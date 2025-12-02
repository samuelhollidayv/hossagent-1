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
import random


async def run_bizdev_cycle(session: Session) -> str:
    """
    BizDev Cycle: Generate 1-2 realistic leads with corporate names.
    
    Safe to call repeatedly - just creates new leads each time.
    In future: will send real outbound emails if SMTP config is present.
    """
    companies = [
        "Stratton Industries",
        "Nexus Capital Partners",
        "Meridian Solutions Group",
        "Apex Ventures LLC",
        "Titan Logistics Inc",
        "Quantum Dynamics",
        "Atlas Enterprise Group",
    ]
    niches = ["SaaS", "Enterprise Software", "FinTech", "Operations", "Research", "Analytics"]

    num_leads = random.randint(1, 2)
    created = []

    for _ in range(num_leads):
        company = random.choice(companies)
        niche = random.choice(niches)
        lead = Lead(
            name=f"Lead_{random.randint(1000, 9999)}",
            email=f"contact@{company.lower().replace(' ', '')}.com",
            company=company,
            niche=niche,
            status="new",
            last_contacted_at=datetime.utcnow(),
        )
        session.add(lead)
        created.append(lead.company)

    session.commit()
    msg = f"BizDev: Generated {num_leads} leads. Companies: {', '.join(created)}"
    print(f"[CYCLE] {msg}")
    return msg


async def run_onboarding_cycle(session: Session) -> str:
    """
    Onboarding Cycle: Convert a new/responded lead into a customer.
    Create 1-2 template tasks for the customer.
    Mark lead as qualified.
    
    Idempotent: Skips leads already converted.
    """
    # Find an unqualified lead (new or responded)
    statement = select(Lead).where(
        Lead.status.in_(["new", "responded"])
    ).limit(1)
    lead = session.exec(statement).first()

    if not lead:
        msg = "Onboarding: No unqualified leads available."
        print(f"[CYCLE] {msg}")
        return msg

    # Check if this lead already has a customer
    existing_customer = session.exec(
        select(Customer).where(Customer.contact_email == lead.email)
    ).first()
    if existing_customer:
        msg = f"Onboarding: Lead {lead.company} already converted to customer {existing_customer.id}."
        print(f"[CYCLE] {msg}")
        return msg

    # Convert lead to customer
    customer = Customer(
        company=lead.company,
        contact_email=lead.email,
        plan="starter",
        billing_plan="starter",
        status="active",
        notes=f"Converted from lead: {lead.company}",
    )
    session.add(customer)
    session.flush()  # Ensure customer gets an ID

    # Create template tasks
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

    # Update lead status
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
    # Find next pending task
    statement = select(Task).where(Task.status == "pending").limit(1)
    task = session.exec(statement).first()

    if not task:
        msg = "Ops: No pending tasks."
        print(f"[CYCLE] {msg}")
        return msg

    # Get customer context
    customer = session.exec(
        select(Customer).where(Customer.id == task.customer_id)
    ).first()

    # Mark running
    task.status = "running"
    session.add(task)
    session.commit()

    # Simulate OpenAI call (ready for real integration)
    # TODO: Replace with real OpenAI API call when OPENAI_API_KEY is set
    simulated_result = f"Research Summary: Analyzed '{task.description}' for {customer.company if customer else 'Unknown'}. Key findings: market opportunity identified, competitive positioning clear, actionable recommendations provided."
    cost_cents = random.randint(2, 8)
    profit_cents = max(0, task.reward_cents - cost_cents)

    # Update task
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
    # Find customers with completed, uninvoiced tasks
    statement = select(Customer).limit(100)
    customers = session.exec(statement).all()

    invoices_created = 0
    msg_parts = []

    for customer in customers:
        # Find completed tasks for this customer
        task_statement = select(Task).where(
            (Task.customer_id == customer.id) & (Task.status == "done")
        )
        completed_tasks = session.exec(task_statement).all()

        if not completed_tasks:
            continue

        # Calculate total reward (invoice amount)
        total_reward = sum(t.reward_cents for t in completed_tasks)

        if total_reward > 0:
            # Check if invoice already exists for this customer (draft status)
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
