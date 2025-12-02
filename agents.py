"""
Autonomous agents for HossAgent business engine.
Each agent manipulates the database and logs plain, operational activity.
"""
from datetime import datetime
from sqlmodel import Session, select
from models import Lead, Customer, Task, Invoice
import random


def run_bizdev_agent(session: Session):
    """
    BizDev Agent: Generate 1-2 realistic leads with corporate names.
    Logs activity in dry operational language.
    """
    companies = [
        "Stratton Industries",
        "Nexus Capital Partners",
        "Meridian Solutions Group",
        "Apex Ventures LLC",
        "Titan Logistics Inc",
    ]
    niches = ["SaaS", "Enterprise Software", "FinTech", "Operations", "Research"]

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
    return f"BizDev: Generated {num_leads} leads. Companies: {', '.join(created)}"


def run_onboarding_agent(session: Session):
    """
    Onboarding Agent: Convert a responded lead into a customer.
    Create 1-2 template tasks for the customer.
    Mark lead as qualified.
    """
    # Find a lead with status "responded"
    statement = select(Lead).where(Lead.status == "responded").limit(1)
    lead = session.exec(statement).first()

    if not lead:
        # Try "new" leads instead
        statement = select(Lead).where(Lead.status == "new").limit(1)
        lead = session.exec(statement).first()

    if not lead:
        return "Onboarding: No leads available to convert."

    # Convert lead to customer
    customer = Customer(
        company=lead.company,
        contact_email=lead.email,
        plan="starter",
        status="active",
        notes=f"Converted from lead: {lead.company}",
    )
    session.add(customer)
    session.flush()  # Ensure customer gets an ID

    # Create template tasks
    task_descriptions = [
        f"Research market analysis for {lead.company}",
        f"Competitive landscape review for {lead.niche}",
    ]
    for desc in task_descriptions[:random.randint(1, 2)]:
        task = Task(
            customer_id=customer.id,
            description=desc,
            status="pending",
            reward_cents=random.randint(50, 200),
        )
        session.add(task)

    # Update lead status
    lead.status = "qualified"
    session.add(lead)
    session.commit()

    return f"Onboarding: Converted {lead.company} to customer. Created tasks."


def run_ops_agent(session: Session):
    """
    Ops Agent: Pick next pending task, mark running, simulate work, mark done.
    Tracks token cost and calculates profit.

    NOTE: Replace the simulated OpenAI call below with real API integration:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4-mini",
        messages=[{"role": "user", "content": task.description}]
    )
    result = response.choices[0].message.content
    """

    # Find next pending task
    statement = select(Task).where(Task.status == "pending").limit(1)
    task = session.exec(statement).first()

    if not task:
        return "Ops: No pending tasks."

    # Mark running
    task.status = "running"
    session.add(task)
    session.commit()

    # Simulate OpenAI call (replace with real API call above)
    simulated_result = f"Research Summary: Analyzed requirements for '{task.description}'. Key findings: market positioned for growth, competitive advantage identified."
    cost_cents = random.randint(2, 8)
    profit_cents = task.reward_cents - cost_cents

    # Update task
    task.status = "done"
    task.cost_cents = cost_cents
    task.profit_cents = profit_cents
    task.result_summary = simulated_result
    task.completed_at = datetime.utcnow()
    session.add(task)
    session.commit()

    return f"Ops: Completed task {task.id}. Cost: {cost_cents}¢, Profit: {profit_cents}¢"


def run_billing_agent(session: Session):
    """
    Billing Agent: Aggregate completed tasks per customer since last invoice.
    Generate draft invoice records.
    """
    # Find customers with completed, uninvoiced tasks
    statement = select(Customer).limit(10)
    customers = session.exec(statement).all()

    invoices_created = 0

    for customer in customers:
        # Find completed tasks without corresponding invoice
        task_statement = select(Task).where(
            (Task.customer_id == customer.id) & (Task.status == "done")
        )
        completed_tasks = session.exec(task_statement).all()

        if not completed_tasks:
            continue

        # Calculate total profit
        total_profit = sum(t.profit_cents for t in completed_tasks)

        if total_profit > 0:
            # Check if invoice already exists for these tasks
            invoice_statement = select(Invoice).where(
                (Invoice.customer_id == customer.id) & (Invoice.status == "draft")
            )
            existing_invoice = session.exec(invoice_statement).first()

            if not existing_invoice:
                invoice = Invoice(
                    customer_id=customer.id,
                    amount_cents=total_profit,
                    status="draft",
                    notes=f"Generated from {len(completed_tasks)} completed tasks",
                )
                session.add(invoice)
                invoices_created += 1

    session.commit()
    return f"Billing: Generated {invoices_created} draft invoices."
