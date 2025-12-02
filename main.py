"""
HossAgent: Autonomous AI Business Engine
FastAPI backend with autopilot-driven autonomous agents.

Routes:
- /                    → Customer Dashboard (public-facing read-only)
- /admin               → Admin Console (operator controls + autopilot toggle)
- /api/leads           → List all leads (GET)
- /api/customers       → List all customers (GET)
- /api/tasks           → List all tasks (GET)
- /api/invoices        → List all invoices (GET)
- /api/run/*           → Admin endpoints to manually trigger agent cycles
- /customers/{id}      → Customer detail page
- /leads/{id}          → Lead detail page
- /invoices/{id}       → Invoice detail page
- /admin/send-test-email → Test email configuration
"""
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Depends, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from database import create_db_and_tables, get_session, engine
from models import Lead, Customer, Task, Invoice, SystemSettings
from agents import (
    run_bizdev_cycle,
    run_onboarding_cycle,
    run_ops_cycle,
    run_billing_cycle,
)
from email_utils import send_email, get_email_status, get_email_log
from lead_service import generate_new_leads_from_source, get_lead_source_log
from lead_sources import get_lead_source_status

app = FastAPI(title="HossAgent Control Engine")


# ============================================================================
# STARTUP & BACKGROUND AUTOPILOT
# ============================================================================


@app.on_event("startup")
async def startup_event():
    """Initialize database and start autopilot background loop."""
    create_db_and_tables()
    asyncio.create_task(autopilot_loop())
    print("✓ HossAgent started. Autopilot loop active.")


async def autopilot_loop():
    """
    Background task: Runs agent cycles automatically when autopilot is enabled.
    
    Checks SystemSettings.autopilot_enabled every 5 minutes.
    If enabled, runs the full pipeline:
      1. Lead Generation - Fetch new leads from configured source (capped by MAX_NEW_LEADS_PER_CYCLE)
      2. BizDev - Send outreach emails to NEW leads (capped by MAX_EMAILS_PER_CYCLE)
      3. Onboarding - Convert qualified leads to customers
      4. Ops - Execute pending tasks
      5. Billing - Generate invoices for completed work
    
    Safe: Catches and logs exceptions without crashing the loop.
    """
    while True:
        try:
            with Session(engine) as session:
                settings = session.exec(
                    select(SystemSettings).where(SystemSettings.id == 1)
                ).first()

                if settings and settings.autopilot_enabled:
                    print("\n[AUTOPILOT] Starting cycle...")
                    
                    generate_new_leads_from_source(session)
                    
                    await run_bizdev_cycle(session)
                    await run_onboarding_cycle(session)
                    await run_ops_cycle(session)
                    await run_billing_cycle(session)
                    print("[AUTOPILOT] Cycle complete.\n")
                else:
                    print("[AUTOPILOT] Disabled. Waiting...")

        except Exception as e:
            print(f"[AUTOPILOT ERROR] {e}")

        # Sleep 5 minutes between cycles
        await asyncio.sleep(300)


# ============================================================================
# PAGES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
def serve_customer_dashboard(session: Session = Depends(get_session)):
    """Customer Dashboard: Public-facing read-only view of system activity."""
    customers = session.exec(select(Customer)).all()
    leads = session.exec(
        select(Lead).order_by(Lead.created_at.desc()).limit(20)
    ).all()
    tasks = session.exec(
        select(Task).order_by(Task.created_at.desc()).limit(20)
    ).all()
    invoices = session.exec(
        select(Invoice).order_by(Invoice.created_at.desc()).limit(20)
    ).all()

    # Compute aggregates
    total_revenue_cents = sum(i.amount_cents for i in invoices if i.status == "paid")
    outstanding_cents = sum(i.amount_cents for i in invoices if i.status in ["draft", "sent"])
    completed_tasks_count = sum(1 for t in tasks if t.status == "done")
    total_leads_count = len(leads)

    # Build HTML rows
    tasks_rows = ""
    for t in tasks:
        if t.status == "done":
            tasks_rows += f"""
                    <tr>
                        <td>{t.created_at.strftime("%Y-%m-%d")}</td>
                        <td><a href="/tasks/{t.id}">{t.description[:50]}</a></td>
                        <td><span class="status-badge done">done</span></td>
                        <td style="text-align: right;" class="money">${t.profit_cents/100:.2f}</td>
                    </tr>
            """
    if not tasks_rows:
        tasks_rows = '<tr><td colspan="4" class="empty">No completed tasks yet.</td></tr>'

    invoices_rows = ""
    for i in invoices:
        cust = next((c.company for c in customers if c.id == i.customer_id), "Unknown")
        status_class = "paid" if i.status == "paid" else "draft"
        invoices_rows += f"""
                    <tr>
                        <td><a href="/invoices/{i.id}">{i.id}</a></td>
                        <td>{cust}</td>
                        <td>${i.amount_cents/100:.2f}</td>
                        <td><span class="status-badge {status_class}">{i.status}</span></td>
                        <td style="text-align: right;">{i.created_at.strftime("%Y-%m-%d")}</td>
                    </tr>
        """
    if not invoices_rows:
        invoices_rows = '<tr><td colspan="5" class="empty">No invoices yet.</td></tr>'

    leads_rows = ""
    for l in leads:
        leads_rows += f"""
                    <tr>
                        <td><a href="/leads/{l.id}">{l.company}</a></td>
                        <td>{l.niche}</td>
                        <td><span class="status-badge">{l.status}</span></td>
                        <td style="text-align: right;">{l.last_contacted_at.strftime("%Y-%m-%d") if l.last_contacted_at else "—"}</td>
                    </tr>
        """
    if not leads_rows:
        leads_rows = '<tr><td colspan="4" class="empty">No leads yet.</td></tr>'

    with open("templates/dashboard.html", "r") as f:
        template = f.read()

    # Simple template substitution
    html = template.format(
        total_revenue=f"${total_revenue_cents/100:.2f}",
        outstanding=f"${outstanding_cents/100:.2f}",
        completed_tasks=completed_tasks_count,
        total_leads=total_leads_count,
        tasks_rows=tasks_rows,
        invoices_rows=invoices_rows,
        leads_rows=leads_rows,
    )
    return html


@app.get("/admin", response_class=HTMLResponse)
def serve_admin_console(session: Session = Depends(get_session)):
    """Admin Console: Operator controls for system management."""
    with open("templates/admin_console.html", "r") as f:
        return f.read()


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
    """Get current system settings including email configuration."""
    settings = session.exec(
        select(SystemSettings).where(SystemSettings.id == 1)
    ).first()
    email_status = get_email_status()
    
    if settings:
        return {
            "autopilot_enabled": settings.autopilot_enabled,
            "email": email_status
        }
    return {"error": "Settings not found", "email": email_status}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
