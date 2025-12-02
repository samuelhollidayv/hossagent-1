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
    website: Optional[str] = None
    source: Optional[str] = None  # dummy_seed, search_api, manual
    last_contacted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Customer(SQLModel, table=True):
    """
    Customer model with subscription/trial support and authentication.
    
    Plan Types:
    - "trial": 7-day restricted trial (limited tasks/leads, DRY_RUN email, no billing)
    - "paid": Full access with $99/month subscription
    - "trial_expired": Trial ended without upgrade
    
    Subscription Status:
    - "none": No active subscription
    - "active": Subscription active
    - "past_due": Payment failed
    - "canceled": Subscription canceled
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company: str
    contact_email: str
    contact_name: Optional[str] = None
    
    password_hash: Optional[str] = None
    
    plan: str = "trial"  # trial, paid, trial_expired
    billing_plan: str = "starter"  # starter, pro, enterprise (legacy)
    status: str = "active"  # active, trial, paused (legacy)
    
    trial_start_at: Optional[datetime] = None
    trial_end_at: Optional[datetime] = None
    
    subscription_status: str = "none"  # none, active, past_due, canceled
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    
    niche: Optional[str] = None
    geography: Optional[str] = None
    
    tasks_this_period: int = Field(default=0)
    leads_this_period: int = Field(default=0)
    
    public_token: Optional[str] = None  # For customer portal access
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TrialIdentity(SQLModel, table=True):
    """
    Trial abuse prevention tracking.
    Records fingerprints to prevent multiple trial signups from same user/device.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    ip_address: Optional[str] = Field(default=None, index=True)
    user_agent_hash: Optional[str] = None
    device_fingerprint: Optional[str] = Field(default=None, index=True)
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    blocked: bool = Field(default=False)
    block_reason: Optional[str] = None
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
    payment_url: Optional[str] = None  # Stripe payment link URL
    stripe_payment_id: Optional[str] = None  # Stripe payment link ID
    created_at: datetime = Field(default_factory=datetime.utcnow)
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None


class Signal(SQLModel, table=True):
    """
    Signals Engine: Captures external context signals about companies.
    
    Sources include competitor updates, job postings, reviews, permits, weather events.
    Each signal can generate one or more LeadEvents for actionable opportunities.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    source_type: str  # job_posting, review, competitor_update, permit, weather, news
    raw_payload: str  # JSON string of raw signal data
    context_summary: Optional[str] = None  # LLM-generated summary
    geography: Optional[str] = None  # Miami, Broward, etc.
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LeadEvent(SQLModel, table=True):
    """
    Signals Engine: Actionable opportunities derived from Signals.
    
    Each event represents a contextual moment for outreach.
    Categories are Miami-tuned: HURRICANE_SEASON, COMPETITOR_SHIFT, GROWTH_SIGNAL, etc.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    signal_id: Optional[int] = Field(default=None, foreign_key="signal.id")
    summary: str  # Human-readable opportunity description
    category: str  # growth, risk, competitor_move, opportunity, hurricane_season, bilingual_opportunity
    urgency_score: int = Field(default=50)  # 0-100, higher = more urgent
    status: str = "new"  # new, queued, contacted, responded, archived
    recommended_action: Optional[str] = None  # What the system suggests
    outbound_message: Optional[str] = None  # Generated email if contacted
    created_at: datetime = Field(default_factory=datetime.utcnow)


TRIAL_TASK_LIMIT = 15
TRIAL_LEAD_LIMIT = 20
SUBSCRIPTION_PRICE_CENTS = 9900  # $99/month
TRIAL_DAYS = 7
