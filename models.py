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
    source: Optional[str] = None  # signalnet, hossnative, manual
    
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
    outreach_style: str = Field(default="transparent_ai")  # transparent_ai or classic
    autopilot_enabled: bool = Field(default=True)  # Per-customer autopilot toggle
    
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
    
    Can be linked to a LeadEvent via lead_event_id for opportunity-specific reports.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id")
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    lead_event_id: Optional[int] = Field(default=None, foreign_key="leadevent.id")
    
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


SIGNAL_STATUS_ACTIVE = "ACTIVE"
SIGNAL_STATUS_DISCARDED = "DISCARDED"
SIGNAL_STATUS_PROMOTED = "PROMOTED"


class Signal(SQLModel, table=True):
    """
    Signals Engine: Captures external context signals about companies.
    
    Sources include competitor updates, job postings, reviews, permits, weather events.
    Each signal can generate one or more LeadEvents for actionable opportunities.
    
    Status:
    - ACTIVE: Signal is active and visible
    - DISCARDED: Signal was manually discarded by admin
    - PROMOTED: Signal was manually promoted to a LeadEvent
    
    Metadata stores extracted contact info from source (URLs, emails, phones).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    source_type: str  # job_posting, review, competitor_update, permit, weather, news
    raw_payload: str  # JSON string of raw signal data
    context_summary: Optional[str] = None  # LLM-generated summary
    geography: Optional[str] = None  # Miami, Broward, etc.
    status: str = Field(default="ACTIVE")  # ACTIVE, DISCARDED, PROMOTED
    noisy_pattern: bool = Field(default=False)  # Flagged as noisy source pattern
    extracted_contact_info: Optional[str] = None  # JSON string: {extracted_urls, extracted_emails, extracted_phones, source_confidence}
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Enrichment status constants - Lifecycle states for LeadEvents
# Customer-facing states: ENRICHED_NO_OUTBOUND (review mode), OUTBOUND_SENT
# Admin-only states: UNENRICHED, WITH_DOMAIN_NO_EMAIL, WITH_PHONE_ONLY
ENRICHMENT_STATUS_UNENRICHED = "UNENRICHED"  # No domain discovered yet
ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL = "WITH_DOMAIN_NO_EMAIL"  # Domain found, email not yet discovered
ENRICHMENT_STATUS_WITH_PHONE_ONLY = "WITH_PHONE_ONLY"  # Phone found but no email (PHONESTORM)
ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND = "ENRICHED_NO_OUTBOUND"  # Email found, awaiting outbound
ENRICHMENT_STATUS_OUTBOUND_SENT = "OUTBOUND_SENT"  # Outbound email sent successfully
ENRICHMENT_STATUS_ARCHIVED = "ARCHIVED"  # Archived (stale or manually archived)
ENRICHMENT_STATUS_ARCHIVED_UNENRICHABLE = "ARCHIVED_UNENRICHABLE"  # Exhausted all enrichment attempts

# Unenrichable reason constants - Why a lead cannot be enriched
UNENRICHABLE_REASON_NO_DOMAIN = "NO_DOMAIN"  # Could not discover domain after max attempts
UNENRICHABLE_REASON_NO_CONTACT_INFO = "NO_CONTACT_INFO"  # Domain found but no email/phone discovered
UNENRICHABLE_REASON_NO_OSINT_PRESENCE = "NO_OSINT_PRESENCE"  # No web presence found
UNENRICHABLE_REASON_BLOCKED_DOMAIN = "BLOCKED_DOMAIN"  # Domain is in blocked list
UNENRICHABLE_REASON_INVALID_COMPANY = "INVALID_COMPANY"  # Company name extraction failed completely

# Legacy status mappings (for backward compatibility during transition)
ENRICHMENT_STATUS_ENRICHING = "ENRICHING"  # Deprecated - maps to UNENRICHED
ENRICHMENT_STATUS_ENRICHED = "ENRICHED"  # Deprecated - maps to ENRICHED_NO_OUTBOUND
ENRICHMENT_STATUS_OUTBOUND_READY = "OUTBOUND_READY"  # Deprecated - maps to ENRICHED_NO_OUTBOUND
ENRICHMENT_STATUS_FAILED = "FAILED"  # Deprecated - maps to UNENRICHED (retry)
ENRICHMENT_STATUS_SKIPPED = "SKIPPED"  # Deprecated - maps to UNENRICHED (no domain)

# Default enrichment budget
DEFAULT_MAX_ENRICHMENT_ATTEMPTS = 3


class LeadEvent(SQLModel, table=True):
    """
    Signals Engine: Actionable opportunities derived from Signals.
    
    Each event represents a contextual moment for outreach.
    Categories are Miami-tuned: HURRICANE_SEASON, COMPETITOR_SHIFT, GROWTH_SIGNAL, etc.
    
    Lifecycle States (Domain-First Pipeline):
    - UNENRICHED: No domain discovered yet (admin-only)
    - WITH_DOMAIN_NO_EMAIL: Domain found but no email discovered (admin-only)
    - ENRICHED_NO_OUTBOUND: Email found, awaiting outbound (review mode visible)
    - OUTBOUND_SENT: Outbound email sent (customer visible)
    - ARCHIVED: Stale or manually archived (hidden)
    
    Customer Portal only shows: OUTBOUND_SENT (and ENRICHED_NO_OUTBOUND in REVIEW mode)
    Admin Console shows all states for debugging.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="customer.id")  # Customer who owns this lead
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    signal_id: Optional[int] = Field(default=None, foreign_key="signal.id")
    macro_event_id: Optional[int] = Field(default=None, foreign_key="macroevent.id")  # EPIC 5: MacroStorm source
    company_table_id: Optional[int] = Field(default=None, foreign_key="company.id")  # EPIC 2: Canonical company link
    lead_name: Optional[str] = None
    lead_email: Optional[str] = None
    lead_company: Optional[str] = None
    lead_domain: Optional[str] = None
    summary: str  # Human-readable opportunity description
    category: str  # growth, risk, competitor_move, opportunity, hurricane_season, bilingual_opportunity
    urgency_score: int = Field(default=50)  # 0-100, higher = more urgent
    status: str = "NEW"  # NEW, CONTACTED, RESPONDED, QUALIFIED, CLOSED_WON, CLOSED_LOST, ON_HOLD
    recommended_action: Optional[str] = None  # What the system suggests
    outbound_message: Optional[str] = None  # Generated email if contacted
    outbound_subject: Optional[str] = None  # Subject line of sent email
    
    enrichment_status: Optional[str] = Field(default="UNENRICHED")  # UNENRICHED, ENRICHING, ENRICHED, OUTBOUND_READY, FAILED, SKIPPED, ARCHIVED_UNENRICHABLE
    enrichment_source: Optional[str] = None  # hunter, clearbit, scrape, signal, manual
    enrichment_attempts: int = Field(default=0)
    max_enrichment_attempts: int = Field(default=3)  # ARCHANGEL v2: Budget limit for attempts
    last_enrichment_at: Optional[datetime] = None
    enriched_email: Optional[str] = None
    enriched_phone: Optional[str] = None
    enriched_contact_name: Optional[str] = None
    enriched_company_name: Optional[str] = None
    enriched_social_links: Optional[str] = None  # JSON string of social links (legacy)
    enriched_at: Optional[datetime] = None
    
    # ARCHANGEL v2: Unenrichable tracking
    unenrichable_reason: Optional[str] = None  # NO_DOMAIN, NO_CONTACT_INFO, NO_OSINT_PRESENCE, BLOCKED_DOMAIN, INVALID_COMPANY
    
    # ARCHANGEL v2: Mission Log - JSON array of enrichment attempt records
    enrichment_mission_log: Optional[str] = None  # JSON: [{timestamp, pass, phase, action, query, result, notes}]
    
    # ARCHANGEL v2: Multi-candidate company names
    candidate_company_names: Optional[str] = None  # JSON: [{name, confidence, source, raw_match}]
    
    # ARCHANGEL Confidence Scoring
    company_name_candidate: Optional[str] = None  # Extracted company name from signal/title (best candidate)
    domain_confidence: float = Field(default=0.0)  # 0-1.0, domain match confidence score
    email_confidence: float = Field(default=0.0)  # 0-1.0, email validity and context score
    social_facebook: Optional[str] = None
    social_instagram: Optional[str] = None
    social_linkedin: Optional[str] = None
    social_twitter: Optional[str] = None
    
    # PHONESTORM: Phone enrichment fields
    lead_phone_raw: Optional[str] = None  # Original extracted phone number
    lead_phone_e164: Optional[str] = None  # Normalized E.164 format (+1XXXYYYZZZZ)
    phone_confidence: float = Field(default=0.0)  # 0-1.0, phone validity score
    phone_source: Optional[str] = None  # contact_page, homepage, footer, schema, tel_link
    phone_type: Optional[str] = None  # mobile, landline, voip, tollfree, unknown
    
    last_contact_at: Optional[datetime] = None
    last_contact_summary: Optional[str] = None
    next_step: Optional[str] = None
    next_step_owner: Optional[str] = None  # AGENT or CUSTOMER
    
    do_not_contact: bool = Field(default=False)
    do_not_contact_reason: Optional[str] = None
    do_not_contact_at: Optional[datetime] = None
    contact_count_24h: int = Field(default=0)
    contact_count_7d: int = Field(default=0)
    last_subject_hash: Optional[str] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


TRIAL_TASK_LIMIT = 15
TRIAL_LEAD_LIMIT = 20
SUBSCRIPTION_PRICE_CENTS = 9900  # $99/month
TRIAL_DAYS = 7

MAX_OUTBOUND_PER_LEAD_PER_DAY = 1
MAX_OUTBOUND_PER_LEAD_PER_WEEK = 3
MAX_OUTBOUND_PER_CUSTOMER_PER_DAY = 100

OUTREACH_STYLE_TRANSPARENT = "transparent_ai"
OUTREACH_STYLE_CLASSIC = "classic"

OPT_OUT_PHRASES = [
    "no thanks", "no thank you", "unsubscribe", "stop", "remove me",
    "remove my email", "don't contact", "do not contact", "please stop",
    "take me off", "opt out", "not interested", "leave me alone"
]


class SignalLog(SQLModel, table=True):
    """
    Structured logging for signal source activity.
    
    Captures all signal pipeline operations for debugging and monitoring.
    Actions: fetch, parse, score, persist, error, dry_run, auto_disable, reset
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source_name: str = Field(index=True)
    action: str = Field(index=True)
    details: Optional[str] = None
    signal_count: int = Field(default=0)
    error_message: Optional[str] = None
    dry_run: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# CONVERSATION ENGINE MODELS
# ============================================================

# Thread status constants
THREAD_STATUS_OPEN = "OPEN"
THREAD_STATUS_HUMAN_OWNED = "HUMAN_OWNED"
THREAD_STATUS_AUTO = "AUTO"
THREAD_STATUS_CLOSED = "CLOSED"

# Message direction constants
MESSAGE_DIRECTION_INBOUND = "INBOUND"
MESSAGE_DIRECTION_OUTBOUND = "OUTBOUND"

# Message status constants (for outbound)
MESSAGE_STATUS_QUEUED = "QUEUED"
MESSAGE_STATUS_SENT = "SENT"
MESSAGE_STATUS_DRAFT = "DRAFT"
MESSAGE_STATUS_FAILED = "FAILED"
MESSAGE_STATUS_APPROVED = "APPROVED"

# Message generated by
MESSAGE_GENERATED_AI = "AI"
MESSAGE_GENERATED_HUMAN = "HUMAN"
MESSAGE_GENERATED_SYSTEM = "SYSTEM"

# Auto-reply level
AUTO_REPLY_NONE = "NONE"
AUTO_REPLY_SAFE_ONLY = "SAFE_ONLY"
AUTO_REPLY_AGGRESSIVE = "AGGRESSIVE"


class Thread(SQLModel, table=True):
    """
    Conversation thread linking all messages between HossAgent and a lead.
    
    Each LeadEvent can have one primary thread.
    All inbound/outbound messages for a lead+customer share the same thread.
    
    Status:
    - OPEN: Active conversation, AI can auto-respond (if allowed)
    - HUMAN_OWNED: Customer took over, AI proposes drafts but doesn't auto-send
    - AUTO: Thread in automated mode (AI handles responses)
    - CLOSED: Thread is closed (opted out, completed, etc.)
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id", index=True)
    lead_event_id: Optional[int] = Field(default=None, foreign_key="leadevent.id", index=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    
    status: str = Field(default="OPEN", index=True)  # OPEN, HUMAN_OWNED, AUTO, CLOSED
    
    lead_email: Optional[str] = Field(default=None, index=True)
    lead_name: Optional[str] = None
    lead_company: Optional[str] = None
    
    last_message_at: Optional[datetime] = None
    last_direction: Optional[str] = None  # INBOUND or OUTBOUND
    last_summary: Optional[str] = None  # Short preview for UI
    
    message_count: int = Field(default=0)
    inbound_count: int = Field(default=0)
    outbound_count: int = Field(default=0)
    
    first_response_at: Optional[datetime] = None  # When lead first replied
    response_time_seconds: Optional[int] = None  # Time to first reply
    
    closed_reason: Optional[str] = None  # opt_out, completed, manual, etc.
    closed_at: Optional[datetime] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Message(SQLModel, table=True):
    """
    Individual email message in a thread (inbound or outbound).
    
    Stores both received replies and sent/draft outbound messages.
    
    Direction:
    - INBOUND: Received from lead
    - OUTBOUND: Sent to lead (or draft)
    
    Status (outbound only):
    - QUEUED: Ready to send
    - SENT: Successfully sent
    - DRAFT: AI-generated, awaiting approval
    - FAILED: Send failed
    - APPROVED: Approved by customer, ready to send
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    thread_id: Optional[int] = Field(default=None, foreign_key="thread.id", index=True)
    lead_id: Optional[int] = Field(default=None, foreign_key="lead.id")
    lead_event_id: Optional[int] = Field(default=None, foreign_key="leadevent.id")
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True)
    
    direction: str = Field(index=True)  # INBOUND or OUTBOUND
    
    from_email: str
    to_email: str
    cc: Optional[str] = None  # JSON array or comma-separated
    reply_to: Optional[str] = None
    
    subject: str
    body_text: str
    body_html: Optional[str] = None
    
    # Email threading headers
    message_id: Optional[str] = Field(default=None, index=True)  # Email Message-ID header
    in_reply_to: Optional[str] = Field(default=None, index=True)  # References header
    references: Optional[str] = None  # Full references chain
    
    # Outbound-specific fields
    status: Optional[str] = Field(default=None, index=True)  # QUEUED, SENT, DRAFT, FAILED, APPROVED
    generated_by: Optional[str] = None  # AI, HUMAN, SYSTEM
    
    # Guardrails
    guardrail_flags: Optional[str] = None  # JSON: which guardrails triggered
    guardrail_approved: bool = Field(default=False)  # Human approved despite guardrails
    
    # Metadata
    raw_metadata: Optional[str] = None  # JSON: headers, MIME info, etc.
    sendgrid_message_id: Optional[str] = None  # SendGrid X-Message-Id for tracking
    
    sent_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None  # customer_id or "admin"
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Suppression(SQLModel, table=True):
    """
    Global and per-customer suppression list.
    
    Prevents any future outbound to emails/domains on this list.
    Can be triggered by:
    - Opt-out keywords in replies
    - Manual addition by customer
    - Bounce/complaint handling
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: Optional[int] = Field(default=None, foreign_key="customer.id", index=True)
    
    email: Optional[str] = Field(default=None, index=True)  # Specific email to suppress
    domain: Optional[str] = Field(default=None, index=True)  # Entire domain to suppress
    
    reason: str  # opt_out, bounce, complaint, manual, etc.
    source_message_id: Optional[int] = Field(default=None, foreign_key="message.id")
    source_thread_id: Optional[int] = Field(default=None, foreign_key="thread.id")
    
    is_global: bool = Field(default=False)  # True = applies to all customers
    
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ConversationMetrics(SQLModel, table=True):
    """
    Aggregated conversation metrics per customer.
    
    Updated periodically to track performance.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", unique=True, index=True)
    
    # Lead funnel
    total_lead_events: int = Field(default=0)
    total_threads: int = Field(default=0)
    
    # Reply rates
    leads_contacted: int = Field(default=0)
    leads_replied: int = Field(default=0)
    reply_rate_pct: float = Field(default=0.0)  # (replied / contacted) * 100
    
    # Response time
    avg_response_time_seconds: Optional[int] = None
    
    # Message volume
    total_outbound: int = Field(default=0)
    total_inbound: int = Field(default=0)
    
    # AI vs Human
    messages_ai_drafted: int = Field(default=0)
    messages_ai_sent_auto: int = Field(default=0)
    messages_human_edited: int = Field(default=0)
    messages_human_sent: int = Field(default=0)
    
    # Thread outcomes
    threads_human_owned: int = Field(default=0)
    threads_closed_opt_out: int = Field(default=0)
    threads_closed_completed: int = Field(default=0)
    
    # Depth
    avg_thread_depth: float = Field(default=0.0)  # Avg messages per thread
    
    last_calculated_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# EPIC 2: COMPANY INTELLIGENCE LAYER
# ============================================================

class Company(SQLModel, table=True):
    """
    ARCHANGEL v2: Canonical company entity.
    
    Stores enriched company data that can be reused across multiple LeadEvents.
    When enrichment succeeds, we upsert to this table and link leads to it.
    This builds our own mini-ZoomInfo over time.
    
    Matching strategy:
    - Primary: normalized_name + geography
    - Secondary: domain (unique identifier)
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    name: str = Field(index=True)
    normalized_name: str = Field(index=True)  # Lowercase, stripped, for matching
    domain: Optional[str] = Field(default=None, index=True, unique=True)  # Primary domain
    
    hq_city: Optional[str] = None
    hq_state: Optional[str] = None
    hq_country: str = Field(default="US")
    geography: Optional[str] = None  # Miami, Broward, Palm Beach, etc.
    
    phones: Optional[str] = None  # JSON: [{number, type, source_url, confidence}]
    emails: Optional[str] = None  # JSON: [{email, type, source_url, confidence}]
    
    source_confidence: float = Field(default=0.0)  # Overall data quality score
    source_signal_id: Optional[int] = Field(default=None, foreign_key="signal.id")
    source_type: Optional[str] = None  # news, craigslist, job_board, reddit, sec_filing
    
    tags: Optional[str] = None  # JSON: ["HVAC", "Miami", "commercial"]
    niche: Optional[str] = None  # Industry/vertical
    
    enrichment_complete: bool = Field(default=False)
    last_enriched_at: Optional[datetime] = None
    enrichment_attempts: int = Field(default=0)
    
    first_seen_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# EPIC 4: ENRICHMENT METRICS
# ============================================================

class EnrichmentMetrics(SQLModel, table=True):
    """
    ARCHANGEL v2: Global enrichment performance metrics.
    
    Tracks enrichment success rates per source to:
    - Kill or downrank low-yield sources
    - Tune scoring thresholds
    - Decide where to invest engineering time
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    source_type: str = Field(index=True)  # news, craigslist, job_board, reddit, sec_filing
    
    total_leads: int = Field(default=0)
    enriched_leads: int = Field(default=0)
    enrichment_rate: float = Field(default=0.0)  # enriched_leads / total_leads * 100
    
    domains_discovered: int = Field(default=0)
    emails_discovered: int = Field(default=0)
    phones_discovered: int = Field(default=0)
    
    avg_attempts_per_lead: float = Field(default=0.0)
    
    unenrichable_no_domain: int = Field(default=0)
    unenrichable_no_contact: int = Field(default=0)
    unenrichable_no_osint: int = Field(default=0)
    unenrichable_blocked: int = Field(default=0)
    unenrichable_invalid_company: int = Field(default=0)
    
    outbound_sent: int = Field(default=0)
    replies_received: int = Field(default=0)
    reply_rate: float = Field(default=0.0)  # replies / outbound * 100
    
    period_start: datetime = Field(default_factory=datetime.utcnow)
    period_end: Optional[datetime] = None
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# EPIC 5: MACROSTORM / FORCECAST
# ============================================================

MACRO_FORCE_TYPE_EXPANSION = "EXPANSION"
MACRO_FORCE_TYPE_CONTRACTION = "CONTRACTION"
MACRO_FORCE_TYPE_RESTRUCTURING = "RESTRUCTURING"
MACRO_FORCE_TYPE_MERGER = "MERGER"
MACRO_FORCE_TYPE_BANKRUPTCY = "BANKRUPTCY"
MACRO_FORCE_TYPE_SUPPLY_CHAIN = "SUPPLY_CHAIN"
MACRO_FORCE_TYPE_REGULATORY = "REGULATORY"

MACRO_SOURCE_SEC_10K = "SEC_10K"
MACRO_SOURCE_SEC_10Q = "SEC_10Q"
MACRO_SOURCE_SEC_8K = "SEC_8K"
MACRO_SOURCE_SEC_S1 = "SEC_S1"
MACRO_SOURCE_EARNINGS_CALL = "EARNINGS_CALL"
MACRO_SOURCE_BANKRUPTCY = "BANKRUPTCY"
MACRO_SOURCE_STATE_REGISTRY = "STATE_REGISTRY"


class MacroEvent(SQLModel, table=True):
    """
    EPIC 5: MacroStorm / ForceCast strategic intelligence.
    
    Captures big-company moves from public filings that create downstream
    opportunities for small businesses.
    
    Example: McDonald's 10-K shows 120 new units in Florida over 3 years
    -> Creates opportunities for local HVAC, construction, staffing, etc.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    
    macro_event_id: str = Field(index=True, unique=True)  # e.g., "macro-2025-SEC-MCD-10K-ops-expansion"
    
    source_type: str = Field(index=True)  # SEC_10K, SEC_8K, BANKRUPTCY, etc.
    source_ref: Optional[str] = None  # CIK number, filing ID, etc.
    source_url: Optional[str] = None
    
    company_name: str  # The big company (e.g., "McDonald's Corporation")
    ticker: Optional[str] = Field(default=None, index=True)  # Stock ticker if public
    
    headline: str  # Human-readable summary: "Plans 500 new units, 120 in Florida over 3 years"
    
    geographies: Optional[str] = None  # JSON: ["Florida", "Miami-Dade", "Broward"]
    segments_affected: Optional[str] = None  # JSON: ["QSR", "logistics", "construction"]
    
    force_type: str = Field(index=True)  # EXPANSION, CONTRACTION, MERGER, etc.
    time_horizon: Optional[str] = None  # "1-3_years", "0-12_months", etc.
    
    risk_impact: Optional[str] = None  # JSON: {local_competitors: "INCREASED_COMPETITION", ...}
    
    raw_snippet: Optional[str] = None  # Actual text from filing/transcript
    confidence: float = Field(default=0.0)  # Extraction confidence score
    
    smb_opportunity_segments: Optional[str] = None  # JSON: [{segment, geo, urgency, window}]
    
    leads_generated: int = Field(default=0)
    leads_enriched: int = Field(default=0)
    leads_contacted: int = Field(default=0)
    leads_replied: int = Field(default=0)
    
    processed: bool = Field(default=False)
    processed_at: Optional[datetime] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
