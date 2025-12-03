from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class SystemSettings(SQLModel, table=True):
    """Global system configuration flags."""
    id: Optional[int] = Field(default=None, primary_key=True)
    autopilot_enabled: bool = Field(default=True)


LEAD_STATUS_NEW = "NEW"
LEAD_STATUS_CONTACTED = "CONTACTED"
LEAD_STATUS_RESPONDED = "RESPONDED"
LEAD_STATUS_QUALIFIED = "QUALIFIED"
LEAD_STATUS_CLOSED_WON = "CLOSED_WON"
LEAD_STATUS_CLOSED_LOST = "CLOSED_LOST"
LEAD_STATUS_ON_HOLD = "ON_HOLD"

NEXT_STEP_OWNER_AGENT = "AGENT"
NEXT_STEP_OWNER_CUSTOMER = "CUSTOMER"


class Lead(SQLModel, table=True):
    """
    Lead model with full lifecycle tracking.
    
    Status Flow:
    NEW -> CONTACTED -> RESPONDED -> QUALIFIED -> CLOSED_WON/CLOSED_LOST
    Any status can move to ON_HOLD
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    company: str
    niche: str
    status: str = "NEW"  # NEW, CONTACTED, RESPONDED, QUALIFIED, CLOSED_WON, CLOSED_LOST, ON_HOLD
    website: Optional[str] = None
    source: Optional[str] = None  # dummy_seed, search_api, manual
    
    last_contacted_at: Optional[datetime] = None
    last_contact_summary: Optional[str] = None
    next_step: Optional[str] = None
    next_step_owner: Optional[str] = None  # AGENT or CUSTOMER
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


OUTREACH_MODE_AUTO = "AUTO"
OUTREACH_MODE_REVIEW = "REVIEW"


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
    
    Outreach Mode:
    - "AUTO": System sends emails automatically
    - "REVIEW": Customer must approve each outbound before sending
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company: str
    contact_email: str
    contact_name: Optional[str] = None
    
    password_hash: Optional[str] = None
    
    plan: str = "trial"  # trial, paid, trial_expired
    billing_plan: str = "starter"  # starter, pro, enterprise (legacy)
    billing_method: Optional[str] = None  # stripe, admin_override
    status: str = "active"  # active, trial, paused (legacy)
    
    trial_start_at: Optional[datetime] = None
    trial_end_at: Optional[datetime] = None
    
    subscription_status: str = "none"  # none, active, past_due, canceled
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    cancelled_at_period_end: bool = Field(default=False)
    cancellation_effective_at: Optional[datetime] = None
    
    outreach_mode: str = Field(default="AUTO")  # AUTO or REVIEW
    
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


class BusinessProfile(SQLModel, table=True):
    """
    Business profile for a customer - defines how outreach is personalized.
    
    1:1 relationship with Customer.
    Used for CC/Reply-To, voice/tone, do-not-contact list, etc.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", unique=True)
    
    short_description: Optional[str] = None
    services: Optional[str] = None  # JSON or comma-separated
    pricing_notes: Optional[str] = None
    ideal_customer: Optional[str] = None
    excluded_customers: Optional[str] = None
    
    voice_tone: Optional[str] = None  # e.g., "professional", "friendly", "casual"
    communication_style: Optional[str] = None  # e.g., "formal", "conversational"
    constraints: Optional[str] = None  # Any restrictions on messaging
    
    primary_contact_name: Optional[str] = None
    primary_contact_email: Optional[str] = None  # Used for CC + Reply-To
    
    do_not_contact_list: Optional[str] = None  # JSON array or comma-separated emails/domains
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Report(SQLModel, table=True):
    """
    Generated reports for customers.
    
    Reports can be research summaries, competitive analyses, market insights, etc.
    Displayed in the portal under "Reports / Recent Work" section.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    
    title: str
    description: Optional[str] = None
    content: Optional[str] = None  # Full report content or JSON
    report_type: str = "general"  # research, competitive, market, opportunity
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PendingOutbound(SQLModel, table=True):
    """
    Pending outbound emails for REVIEW mode customers.
    
    When outreach_mode="REVIEW", emails are queued here for customer approval.
    Actions: PENDING, APPROVED, SKIPPED, SENT
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    lead_event_id: Optional[int] = Field(default=None, foreign_key="leadevent.id")
    
    to_email: str
    to_name: Optional[str] = None
    subject: str
    body: str
    
    context_summary: Optional[str] = None  # Why this email is being sent
    
    status: str = "PENDING"  # PENDING, APPROVED, SKIPPED, SENT
    approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    skipped_reason: Optional[str] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PasswordResetToken(SQLModel, table=True):
    """
    Password reset tokens for forgot password flow.
    
    Tokens expire after 1 hour.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    token: str = Field(index=True)
    expires_at: datetime
    used: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


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
    
    Lifecycle tracking mirrors Lead lifecycle for consistency.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    signal_id: Optional[int] = Field(default=None, foreign_key="signal.id")
    summary: str  # Human-readable opportunity description
    category: str  # growth, risk, competitor_move, opportunity, hurricane_season, bilingual_opportunity
    urgency_score: int = Field(default=50)  # 0-100, higher = more urgent
    status: str = "NEW"  # NEW, CONTACTED, RESPONDED, QUALIFIED, CLOSED_WON, CLOSED_LOST, ON_HOLD
    recommended_action: Optional[str] = None  # What the system suggests
    outbound_message: Optional[str] = None  # Generated email if contacted
    
    last_contact_at: Optional[datetime] = None
    last_contact_summary: Optional[str] = None
    next_step: Optional[str] = None
    next_step_owner: Optional[str] = None  # AGENT or CUSTOMER
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


TRIAL_TASK_LIMIT = 15
TRIAL_LEAD_LIMIT = 20
SUBSCRIPTION_PRICE_CENTS = 9900  # $99/month
TRIAL_DAYS = 7
