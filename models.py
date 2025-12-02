from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class SystemSettings(SQLModel, table=True):
    """Global system configuration flags."""
    id: Optional[int] = Field(default=None, primary_key=True)
    autopilot_enabled: bool = Field(default=True)


class Lead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    company: str
    niche: str
    status: str = "new"  # new, contacted, responded, qualified, dead
    last_contacted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company: str
    contact_email: str
    plan: str = "starter"
    billing_plan: str = "starter"  # starter, pro, enterprise
    status: str = "active"  # active, trial, paused
    stripe_customer_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    description: str
    status: str = "pending"  # pending, running, done, failed
    reward_cents: int
    cost_cents: int = 0
    profit_cents: int = 0
    result_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class Invoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    amount_cents: int
    status: str = "draft"  # draft, sent, paid
    created_at: datetime = Field(default_factory=datetime.utcnow)
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None
