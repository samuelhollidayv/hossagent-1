"""
Signals Agent - The Ethical Briefcase System

This agent monitors external context signals about companies and generates
actionable LeadEvents for moment-aware outreach. It transforms HossAgent
from generic lead gen into a context-aware intelligence engine.

Miami-tuned heuristics included for South Florida market.

============================================================================
SIGNALNET INTEGRATION
============================================================================
The Signals Agent now integrates with the SignalNet framework for real signal
sources (weather, news, Reddit). The SIGNAL_MODE environment variable controls
the pipeline behavior:

  PRODUCTION: Run real sources, create LeadEvents for high-scoring signals
  SANDBOX: Run sources and score signals, but don't create LeadEvents
  OFF: Skip signal ingestion entirely

Default: SANDBOX (safe mode for development)

When SIGNAL_MODE is OFF, the agent skips the entire SignalNet pipeline.

============================================================================
MIAMI BIAS CONFIGURATION
============================================================================
The Signals Engine is configured via two key environment variables:

  LEAD_GEOGRAPHY: Target geographic market (e.g., "Miami, Broward, South Florida")
  LEAD_NICHE: Target industry verticals (e.g., "HVAC, Roofing, Med Spa")

These values affect:
  1. Signal Scoring - Signals matching LEAD_GEOGRAPHY get +15 urgency boost
  2. LeadEvent Creation - Events from target geography are prioritized
  3. Category Assignment - Miami-specific categories (HURRICANE_SEASON, 
     MIAMI_PRICE_MOVE, BILINGUAL_OPPORTUNITY) get higher base weights when
     geography matches South Florida

Default fallbacks if env vars not set:
  LEAD_GEOGRAPHY -> "Miami, Broward, South Florida"
  LEAD_NICHE -> "HVAC, Roofing, Med Spa, Immigration Attorney"
============================================================================
"""

import json
import random
import os
from datetime import datetime
from typing import Optional, Dict, Sequence
from sqlmodel import Session, select

from models import (
    Signal, LeadEvent, Customer, Lead,
    ENRICHMENT_STATUS_OUTBOUND_SENT,
    ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND,
    ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL,
    ENRICHMENT_STATUS_UNENRICHED,
)
from subscription_utils import increment_leads_used
from signal_sources import (
    run_signal_pipeline,
    get_signal_status,
    get_signal_mode,
    SIGNAL_MODE,
    LEAD_GEOGRAPHY as SIGNALNET_GEOGRAPHY,
    LEAD_NICHE as SIGNALNET_NICHE,
)


# ============================================================================
# Miami-first targeting via LEAD_GEOGRAPHY, LEAD_NICHE
# These env vars control the geographic and industry bias of the signals engine
# ============================================================================
LEAD_GEOGRAPHY = os.environ.get("LEAD_GEOGRAPHY", "Miami, Broward, South Florida")
LEAD_NICHE = os.environ.get("LEAD_NICHE", "HVAC, Roofing, Med Spa, Immigration Attorney")

# Parse LEAD_GEOGRAPHY into searchable list for matching
LEAD_GEOGRAPHY_LIST = [g.strip().lower() for g in LEAD_GEOGRAPHY.split(",")]

# Parse LEAD_NICHE into searchable list for industry matching
LEAD_NICHE_LIST = [n.strip().lower() for n in LEAD_NICHE.split(",")]

# Log configuration at module load (startup) - include SignalNet mode
print(f"[SIGNALS][STARTUP] Mode: {SIGNAL_MODE}")
print(f"[SIGNALS][STARTUP] Geography: {LEAD_GEOGRAPHY}, Niche: {LEAD_NICHE}")

def _log_signalnet_sources_status():
    """Log status of SignalNet sources at startup."""
    status = get_signal_status()
    registry = status.get("registry", {})
    sources = registry.get("sources", [])
    
    enabled_sources = [s["name"] for s in sources if s.get("enabled")]
    disabled_sources = [s["name"] for s in sources if not s.get("enabled")]
    
    if enabled_sources:
        print(f"[SIGNALS][STARTUP] Enabled sources: {', '.join(enabled_sources)}")
    if disabled_sources:
        print(f"[SIGNALS][STARTUP] Disabled sources: {', '.join(disabled_sources)}")

_log_signalnet_sources_status()


# Miami-specific industry verticals - high-value niches for South Florida market
MIAMI_INDUSTRIES = [
    "med spa", "hvac", "roofing", "immigration attorney", 
    "realtor", "insurance broker", "marketing agency",
    "dental practice", "auto repair", "landscaping"
]

# Miami/South Florida geographic areas for signal generation
MIAMI_AREAS = [
    "Miami", "Coral Gables", "Brickell", "Wynwood", "Little Havana",
    "Doral", "Hialeah", "Miami Beach", "Fort Lauderdale", "Broward County",
    "Hollywood", "Pembroke Pines", "Aventura", "Kendall", "Homestead"
]


def matches_lead_geography(geography: Optional[str]) -> bool:
    """
    Check if a geography string matches the configured LEAD_GEOGRAPHY.
    
    Miami-first targeting: Returns True if the geography contains any of
    the target areas specified in LEAD_GEOGRAPHY env var.
    
    Args:
        geography: Geographic area string (e.g., "Miami", "Broward County")
    
    Returns:
        True if geography matches LEAD_GEOGRAPHY, False otherwise
    """
    if not geography:
        return False
    geo_lower = geography.lower()
    return any(target in geo_lower for target in LEAD_GEOGRAPHY_LIST)


def matches_lead_niche(niche: Optional[str]) -> bool:
    """
    Check if a niche string matches the configured LEAD_NICHE.
    
    Miami-first targeting: Returns True if the niche contains any of
    the target industries specified in LEAD_NICHE env var.
    
    Args:
        niche: Industry/niche string (e.g., "HVAC", "roofing contractor")
    
    Returns:
        True if niche matches LEAD_NICHE, False otherwise
    """
    if not niche:
        return False
    niche_lower = niche.lower()
    return any(target in niche_lower for target in LEAD_NICHE_LIST)


def infer_category(signal_type: str, context: str) -> str:
    """
    Infer LeadEvent category from signal content.
    
    Miami-tuned categories:
    - HURRICANE_SEASON: Storm/hurricane-related signals (high priority in South FL)
    - MIAMI_PRICE_MOVE: Pricing changes in Miami market
    - BILINGUAL_OPPORTUNITY: Spanish/bilingual signals (critical in Miami market)
    - COMPETITOR_SHIFT: Competitor positioning changes
    - GROWTH_SIGNAL: Hiring/expansion signals
    - REPUTATION_CHANGE: Review-based signals
    - OPPORTUNITY: General opportunity signals
    """
    context_lower = context.lower()
    
    # Miami-specific high-priority categories
    if "hurricane" in context_lower or "storm" in context_lower:
        return "HURRICANE_SEASON"
    elif "bilingual" in context_lower or "spanish" in context_lower:
        return "BILINGUAL_OPPORTUNITY"
    elif "price" in context_lower and ("miami" in context_lower or "local" in context_lower):
        return "MIAMI_PRICE_MOVE"
    # General categories
    elif "competitor" in context_lower or "pricing" in context_lower:
        return "COMPETITOR_SHIFT"
    elif "hiring" in context_lower or "job" in context_lower or "growth" in context_lower:
        return "GROWTH_SIGNAL"
    elif "review" in context_lower:
        return "REPUTATION_CHANGE"
    elif "price" in context_lower or "pricing" in context_lower:
        return "MIAMI_PRICE_MOVE"
    else:
        return "OPPORTUNITY"


def calculate_urgency(signal_type: str, category: str, geography: Optional[str] = None, niche: Optional[str] = None) -> int:
    """
    Calculate urgency score 0-100 based on signal characteristics.
    
    Miami-first targeting via LEAD_GEOGRAPHY, LEAD_NICHE:
    - Base scores are set per category (Miami-tuned categories get higher base)
    - +15 urgency boost if geography matches LEAD_GEOGRAPHY
    - +10 urgency boost if niche matches LEAD_NICHE
    
    Category base weights (Miami-tuned):
    - HURRICANE_SEASON: 75 (highest - critical for South FL)
    - REPUTATION_CHANGE: 70
    - COMPETITOR_SHIFT: 65
    - GROWTH_SIGNAL: 60
    - MIAMI_PRICE_MOVE: 60
    - BILINGUAL_OPPORTUNITY: 55
    - OPPORTUNITY: 50 (default)
    
    Args:
        signal_type: Type of signal source
        category: Inferred category from signal content
        geography: Optional geography for boost calculation
        niche: Optional niche for boost calculation
    
    Returns:
        Urgency score 0-100 (clamped to 30-95 range)
    """
    # Base scores - Miami-tuned categories get higher weights
    base_score = 50
    
    if category == "HURRICANE_SEASON":
        base_score = 75  # Highest priority - critical for South Florida
    elif category == "REPUTATION_CHANGE":
        base_score = 70
    elif category == "COMPETITOR_SHIFT":
        base_score = 65
    elif category == "GROWTH_SIGNAL":
        base_score = 60
    elif category == "MIAMI_PRICE_MOVE":
        base_score = 60
    elif category == "BILINGUAL_OPPORTUNITY":
        base_score = 55
    
    # Miami-first targeting: Boost signals from LEAD_GEOGRAPHY
    geography_boost = 0
    if geography and matches_lead_geography(geography):
        geography_boost = 15
    
    # Boost signals matching LEAD_NICHE industries
    niche_boost = 0
    if niche and matches_lead_niche(niche):
        niche_boost = 10
    
    # Add random variation for natural distribution
    variation = random.randint(-10, 10)
    
    final_score = base_score + geography_boost + niche_boost + variation
    
    return max(30, min(95, final_score))


def generate_recommended_action(category: str, signal_summary: str) -> str:
    """
    Generate recommended action based on category.
    
    Miami-first targeting: Actions are tuned for South Florida market context.
    Categories like HURRICANE_SEASON, BILINGUAL_OPPORTUNITY, and MIAMI_PRICE_MOVE
    have Miami-specific recommended actions.
    """
    actions = {
        "HURRICANE_SEASON": "Offer hurricane-season discount bundle or preparedness package",
        "COMPETITOR_SHIFT": "Send competitive analysis snapshot highlighting your differentiators",
        "GROWTH_SIGNAL": "Propose partnership or capacity-building services",
        "BILINGUAL_OPPORTUNITY": "Highlight bilingual staff on homepage - big ROI in Miami market",
        "REPUTATION_CHANGE": "Offer reputation management or customer experience audit",
        "MIAMI_PRICE_MOVE": "Prepare market pricing comparison and value proposition",
        "OPPORTUNITY": "Send contextual outreach with relevant service offer"
    }
    return actions.get(category, "Prepare contextual outreach based on signal")


def run_signals_agent(session: Session, max_signals: int = 10) -> Dict:
    """
    Run the Signals Agent with SignalNet integration.
    
    Pipeline behavior is controlled by SIGNAL_MODE environment variable:
    
      OFF: Skip SignalNet entirely, log and return immediately
      SANDBOX: Run SignalNet sources, score signals, but don't create LeadEvents
      PRODUCTION: Full pipeline including LeadEvent creation for high-scoring signals
    
    Miami-first targeting via LEAD_GEOGRAPHY, LEAD_NICHE:
    - Signals are scored with Miami-area geography boost (+15)
    - Urgency scores are boosted for signals matching LEAD_NICHE (+10)
    - Miami-tuned categories (HURRICANE_SEASON, etc.) get higher base urgency
    
    Returns dict with counts of signals and events generated, plus source details.
    """
    print(f"[SIGNALS] ============================================================")
    print(f"[SIGNALS] Starting Signals Agent cycle - Mode: {SIGNAL_MODE}")
    print(f"[SIGNALS] Geography: {LEAD_GEOGRAPHY}")
    print(f"[SIGNALS] Niche: {LEAD_NICHE}")
    print(f"[SIGNALS] ============================================================")
    
    if SIGNAL_MODE == "OFF":
        print("[SIGNALS] SIGNAL_MODE is OFF - skipping SignalNet pipeline entirely")
        return {
            "signals_created": 0,
            "events_created": 0,
            "mode": "OFF",
            "skipped": True,
            "message": "SignalNet is disabled (SIGNAL_MODE=OFF)"
        }
    
    print(f"[SIGNALS] Running SignalNet pipeline in {SIGNAL_MODE} mode...")
    
    pipeline_result = run_signal_pipeline(session)
    
    signals_from_pipeline = pipeline_result.get("signals_persisted", 0)
    events_from_pipeline = pipeline_result.get("events_created", 0)
    sources_run = pipeline_result.get("sources_run", [])
    errors = pipeline_result.get("errors", [])
    
    print(f"[SIGNALS] SignalNet pipeline results:")
    print(f"[SIGNALS]   - Sources checked: {pipeline_result.get('sources_checked', 0)}")
    print(f"[SIGNALS]   - Sources eligible: {pipeline_result.get('sources_eligible', 0)}")
    print(f"[SIGNALS]   - Signals fetched: {pipeline_result.get('signals_fetched', 0)}")
    print(f"[SIGNALS]   - Signals persisted: {signals_from_pipeline}")
    print(f"[SIGNALS]   - Events created: {events_from_pipeline}")
    
    for source_result in sources_run:
        source_name = source_result.get("source", "unknown")
        fetched = source_result.get("fetched", 0)
        persisted = source_result.get("persisted", 0)
        events = source_result.get("events_created", 0)
        error = source_result.get("error")
        
        if error:
            print(f"[SIGNALS][{source_name.upper()}] ERROR: {error}")
        else:
            print(f"[SIGNALS][{source_name.upper()}] Fetched: {fetched}, Persisted: {persisted}, Events: {events}")
    
    if errors:
        print(f"[SIGNALS] Pipeline errors:")
        for err in errors:
            print(f"[SIGNALS]   - {err.get('source')}: {err.get('error')}")
    
    print(f"[SIGNALS] Cycle complete. Mode: {SIGNAL_MODE}, Signals: {signals_from_pipeline}, Events: {events_from_pipeline}")
    
    return {
        "signals_created": signals_from_pipeline,
        "events_created": events_from_pipeline,
        "mode": SIGNAL_MODE,
        "source": "signalnet",
        "signalnet_result": pipeline_result,
        "message": f"SignalNet pipeline completed in {SIGNAL_MODE} mode"
    }


def get_todays_opportunities(
    session: Session, 
    company_id: Optional[int] = None, 
    limit: int = 10,
    enrichment_status: Optional[str] = None,
    include_review_mode: bool = False
) -> Sequence[LeadEvent]:
    """
    Get today's opportunities (LeadEvents) for display in customer portal.
    
    By default, only shows OUTBOUND_SENT events (emails that have been sent).
    If include_review_mode=True, also includes ENRICHED_NO_OUTBOUND (pending review).
    
    Sorted by urgency_score desc, then by created_at desc.
    """
    query = select(LeadEvent).order_by(
        LeadEvent.urgency_score.desc(),
        LeadEvent.created_at.desc()
    ).limit(limit)
    
    if company_id:
        query = query.where(LeadEvent.company_id == company_id)
    
    if enrichment_status:
        query = query.where(LeadEvent.enrichment_status == enrichment_status)
    elif include_review_mode:
        query = query.where(LeadEvent.enrichment_status.in_([
            ENRICHMENT_STATUS_OUTBOUND_SENT,
            ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND
        ]))
    else:
        query = query.where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_OUTBOUND_SENT)
    
    return session.exec(query).all()


def get_lead_events_by_enrichment_status(
    session: Session, 
    enrichment_status: str, 
    limit: int = 50
) -> Sequence[LeadEvent]:
    """
    Get LeadEvents filtered by enrichment status for admin console.
    
    Enrichment Status Flow:
    - UNENRICHED: Raw signals, no domain/email yet
    - WITH_DOMAIN_NO_EMAIL: Domain discovered, awaiting email scraping
    - ENRICHED_NO_OUTBOUND: Ready to send (email found)
    - OUTBOUND_SENT: Email sent
    """
    return session.exec(
        select(LeadEvent)
        .where(LeadEvent.enrichment_status == enrichment_status)
        .order_by(LeadEvent.created_at.desc())
        .limit(limit)
    ).all()


def get_lead_events_counts_by_status(session: Session) -> Dict[str, int]:
    """Get counts of LeadEvents by enrichment status for admin dashboard."""
    statuses = [
        ENRICHMENT_STATUS_UNENRICHED,
        ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL,
        ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND,
        ENRICHMENT_STATUS_OUTBOUND_SENT,
    ]
    
    counts = {}
    for status in statuses:
        count = len(session.exec(
            select(LeadEvent).where(LeadEvent.enrichment_status == status)
        ).all())
        counts[status] = count
    
    return counts


def get_signals_summary(session: Session, limit: int = 20) -> Sequence[Signal]:
    """Get recent signals for admin display."""
    return session.exec(
        select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    ).all()


def get_lead_events_summary(session: Session, limit: int = 20) -> Sequence[LeadEvent]:
    """Get recent lead events for admin display."""
    return session.exec(
        select(LeadEvent).order_by(LeadEvent.created_at.desc()).limit(limit)
    ).all()
