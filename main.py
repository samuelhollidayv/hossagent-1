"""
HossAgent: Autonomous AI Business Engine
FastAPI backend with four autonomous agents
"""
from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select
from database import create_db_and_tables, get_session
from models import Lead, Customer, Task, Invoice
from agents import (
    run_bizdev_agent,
    run_onboarding_agent,
    run_ops_agent,
    run_billing_agent,
)

app = FastAPI(title="HossAgent Control Engine")


@app.on_event("startup")
def on_startup():
    """Initialize database on startup."""
    create_db_and_tables()


# ============================================================================
# PAGES
# ============================================================================


@app.get("/", response_class=HTMLResponse)
def serve_landing():
    """Serve the landing page."""
    with open("templates/index.html", "r") as f:
        return f.read()


@app.get("/control", response_class=HTMLResponse)
def serve_control_room():
    """Serve the control room."""
    with open("templates/control_room.html", "r") as f:
        return f.read()


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
            "status": c.status,
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
# API ENDPOINTS - AGENT EXECUTION
# ============================================================================


@app.post("/api/run/bizdev")
def run_bizdev(session: Session = Depends(get_session)):
    """Run the BizDev agent."""
    message = run_bizdev_agent(session)
    return {"message": message}


@app.post("/api/run/onboarding")
def run_onboarding(session: Session = Depends(get_session)):
    """Run the Onboarding agent."""
    message = run_onboarding_agent(session)
    return {"message": message}


@app.post("/api/run/ops")
def run_ops(session: Session = Depends(get_session)):
    """Run the Ops agent."""
    message = run_ops_agent(session)
    return {"message": message}


@app.post("/api/run/billing")
def run_billing(session: Session = Depends(get_session)):
    """Run the Billing agent."""
    message = run_billing_agent(session)
    return {"message": message}


# ============================================================================
# FUTURE HOOKS
# ============================================================================


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: int, session: Session = Depends(get_session)):
    """Placeholder for future task completion logic."""
    return {"message": "Task completion logic will be implemented here."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
