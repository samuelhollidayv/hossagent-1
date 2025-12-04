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
When SignalNet returns 0 signals AND no leads exist in DB, synthetic fallback
signals are generated for demo purposes.

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

from models import Signal, LeadEvent, Customer, Lead
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


def generate_competitor_signal(company: str, niche: str) -> Dict:
    """Generate synthetic competitor update signal."""
    competitor_updates = [
        f"Competitor updated service pricing for core offerings",
        f"Competitor added 24/7 emergency service section to website",
        f"Competitor changed hero banner to hurricane season messaging",
        f"Competitor launched new loyalty program for repeat customers",
        f"Competitor expanded service area to include more neighborhoods",
        f"Competitor added Spanish-language pages to website",
        f"Competitor now offering free consultations / estimates",
        f"Competitor running aggressive social media ad campaign",
    ]
    
    if "hvac" in niche.lower():
        competitor_updates.extend([
            "HVAC competitor promoting AC maintenance specials",
            "Competitor advertising 'Beat the Heat' summer pricing",
        ])
    elif "roofing" in niche.lower():
        competitor_updates.extend([
            "Roofing competitor changed hero banner to 'Hurricane season upgrades'",
            "Competitor offering free roof inspections before storm season",
        ])
    elif "med spa" in niche.lower() or "dental" in niche.lower():
        competitor_updates.extend([
            "Competitor updated pricing for Botox / fillers / facials",
            "Med spa competitor added new membership tier",
        ])
    elif "immigration" in niche.lower() or "attorney" in niche.lower():
        competitor_updates.extend([
            "Immigration attorney added TPS/DACA update page",
            "Competitor highlighting expedited processing services",
        ])
    
    update = random.choice(competitor_updates)
    area = random.choice(MIAMI_AREAS)
    
    return {
        "source_type": "competitor_update",
        "raw_payload": json.dumps({
            "company": company,
            "competitor_name": f"{random.choice(['Premium', 'Elite', 'Pro', 'Express', 'Local'])} {niche.split()[0].title()} Services",
            "update_type": update,
            "area": area,
            "detected_at": datetime.utcnow().isoformat()
        }),
        "context_summary": f"{update} detected for {company}'s competitor in {area}. This may indicate a shift in local market positioning."
    }


def generate_job_posting_signal(company: str, niche: str) -> Dict:
    """Generate synthetic job posting signal."""
    miami_roles = [
        ("Hiring bilingual office assistant", "Coral Gables"),
        ("Looking for experienced technician", "Miami"),
        ("Seeking sales coordinator", "Doral"),
        ("Hiring customer service rep - Spanish required", "Hialeah"),
        ("Estimator / project manager needed", "Broward County"),
        ("Marketing coordinator position open", "Brickell"),
        ("Operations manager wanted", "Fort Lauderdale"),
    ]
    
    role, area = random.choice(miami_roles)
    
    return {
        "source_type": "job_posting",
        "raw_payload": json.dumps({
            "company": company,
            "job_title": role,
            "location": area,
            "bilingual_required": "Spanish" in role or "bilingual" in role.lower(),
            "posted_at": datetime.utcnow().isoformat()
        }),
        "context_summary": f"{company} is {role.lower()} in {area}. This typically signals growth or operational scaling."
    }


def generate_review_signal(company: str, niche: str) -> Dict:
    """Generate synthetic review event signal."""
    review_events = [
        ("positive", "New 5-star review praising fast turnaround and great service"),
        ("positive", "Spanish-language review complimenting bilingual staff"),
        ("positive", "Customer praised emergency response time"),
        ("negative", "Review citing scheduling delays - common pain point"),
        ("negative", "Complaint about pricing transparency"),
        ("neutral", "Mixed review - good work but slow communication"),
    ]
    
    sentiment, summary = random.choice(review_events)
    platform = random.choice(["Google", "Yelp", "Facebook", "BBB"])
    
    return {
        "source_type": "review",
        "raw_payload": json.dumps({
            "company": company,
            "platform": platform,
            "sentiment": sentiment,
            "summary": summary,
            "detected_at": datetime.utcnow().isoformat()
        }),
        "context_summary": f"New {sentiment} review on {platform} for {company}: {summary}"
    }


def generate_local_signal(company: str, niche: str) -> Dict:
    """Generate synthetic local/weather/permit signal."""
    local_events = [
        {
            "type": "weather",
            "summary": "Hurricane preparedness advisory increased local demand for protective services",
            "category": "HURRICANE_SEASON"
        },
        {
            "type": "permit",
            "summary": f"New building permit approved in {random.choice(MIAMI_AREAS)} area",
            "category": "GROWTH_SIGNAL"
        },
        {
            "type": "weather", 
            "summary": "Extended heat wave driving increased HVAC service demand",
            "category": "OPPORTUNITY"
        },
        {
            "type": "local_news",
            "summary": f"New commercial development announced in {random.choice(MIAMI_AREAS)}",
            "category": "GROWTH_SIGNAL"
        },
        {
            "type": "demographics",
            "summary": "Population growth in target area suggests expanding market",
            "category": "OPPORTUNITY"
        }
    ]
    
    event = random.choice(local_events)
    
    return {
        "source_type": event["type"],
        "raw_payload": json.dumps({
            "company": company,
            "event_type": event["type"],
            "summary": event["summary"],
            "category": event["category"],
            "detected_at": datetime.utcnow().isoformat()
        }),
        "context_summary": event["summary"]
    }


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


def _generate_synthetic_fallback_signals(session: Session, max_signals: int = 10) -> Dict:
    """
    Generate synthetic fallback signals for demo purposes.
    
    This is called when:
    - SignalNet pipeline returns 0 signals (all sources cooling down or failing)
    - AND there are no leads in the database
    
    Signals are clearly marked with source="synthetic" to distinguish from real signals.
    
    IMPORTANT: Synthetic signals are generated about EXTERNAL prospect companies,
    NOT about customers using HossAgent. This prevents self-referential signals
    where a customer sees signals about their own business.
    
    Returns dict with counts of signals and events generated.
    """
    print("[SIGNALS][SYNTHETIC] Generating fallback signals for demo purposes...")
    
    customers = session.exec(select(Customer).limit(20)).all()
    leads = session.exec(select(Lead).where(Lead.status != "dead").limit(30)).all()
    
    customer_company_names = {c.company.lower() for c in customers if c.company}
    
    SYNTHETIC_PROSPECT_COMPANIES = [
        {"name": "Sunshine HVAC Solutions", "niche": "HVAC"},
        {"name": "Miami Cooling Experts", "niche": "HVAC"},
        {"name": "Coastal Roofing Pros", "niche": "roofing"},
        {"name": "South Florida Roofers", "niche": "roofing"},
        {"name": "Glow Med Spa Miami", "niche": "med spa"},
        {"name": "Brickell Aesthetics", "niche": "med spa"},
        {"name": "Garcia Immigration Law", "niche": "immigration attorney"},
        {"name": "Coral Gables Realty", "niche": "realtor"},
        {"name": "Doral Marketing Group", "niche": "marketing agency"},
        {"name": "Premier Dental Care", "niche": "dental practice"},
    ]
    
    all_companies = []
    for prospect in SYNTHETIC_PROSPECT_COMPANIES:
        all_companies.append({
            "id": None,
            "name": prospect["name"],
            "niche": prospect["niche"],
            "type": "synthetic_prospect"
        })
    for l in leads:
        if l.company and l.company.lower() not in customer_company_names:
            all_companies.append({
                "id": l.id,
                "name": l.company,
                "niche": l.niche or "small business",
                "type": "lead"
            })
    
    if not all_companies:
        print("[SIGNALS][SYNTHETIC] No companies found. Skipping synthetic signal generation.")
        return {"signals_created": 0, "events_created": 0, "source": "synthetic"}
    
    signals_created = 0
    events_created = 0
    
    num_to_generate = random.randint(5, min(10, len(all_companies)))
    companies_to_process = random.sample(all_companies, num_to_generate)
    
    for company in companies_to_process:
        num_signals = random.randint(1, 2)
            
        signal_generators = [
            generate_competitor_signal,
            generate_job_posting_signal,
            generate_review_signal,
            generate_local_signal
        ]
        
        chosen_generators = random.sample(signal_generators, min(num_signals, len(signal_generators)))
        
        for generator in chosen_generators:
            signal_data = generator(company["name"], company["niche"])
            
            signal_geography = random.choice(MIAMI_AREAS)
            
            raw_payload = json.loads(signal_data["raw_payload"])
            raw_payload["source"] = "synthetic"
            
            signal = Signal(
                company_id=None,
                lead_id=company["id"] if company["type"] == "lead" else None,
                source_type=signal_data["source_type"],
                raw_payload=json.dumps(raw_payload),
                context_summary=signal_data["context_summary"],
                geography=signal_geography
            )
            session.add(signal)
            session.commit()
            session.refresh(signal)
            signals_created += 1
            
            print(f"[SIGNALS][SYNTHETIC][{signal.source_type.upper()}] {company['name']}: {signal.context_summary[:60]}...")
            
            category = infer_category(signal.source_type, signal.context_summary)
            
            urgency = calculate_urgency(
                signal.source_type, 
                category, 
                geography=signal_geography,
                niche=company["niche"]
            )
            
            recommended_action = generate_recommended_action(category, signal.context_summary)
            
            event = LeadEvent(
                company_id=None,
                lead_id=company["id"] if company["type"] == "lead" else None,
                signal_id=signal.id,
                summary=f"[SYNTHETIC] {signal.context_summary}",
                category=category,
                urgency_score=urgency,
                status="NEW",
                recommended_action=recommended_action,
                lead_company=company["name"]
            )
            session.add(event)
            session.commit()
            events_created += 1
            
            geo_match = "✓ GEO" if matches_lead_geography(signal_geography) else ""
            niche_match = "✓ NICHE" if matches_lead_niche(company["niche"]) else ""
            print(f"[SIGNALS][SYNTHETIC][EVENT] {category} (urgency: {urgency}) {geo_match} {niche_match}")
    
    print(f"[SIGNALS][SYNTHETIC] Complete. Created {signals_created} signals, {events_created} events.")
    
    return {
        "signals_created": signals_created,
        "events_created": events_created,
        "source": "synthetic"
    }


def run_signals_agent(session: Session, max_signals: int = 10) -> Dict:
    """
    Run the Signals Agent with SignalNet integration.
    
    Pipeline behavior is controlled by SIGNAL_MODE environment variable:
    
      OFF: Skip SignalNet entirely, log and return immediately
      SANDBOX: Run SignalNet sources, score signals, but don't create LeadEvents
      PRODUCTION: Full pipeline including LeadEvent creation for high-scoring signals
    
    Synthetic Fallback:
      If SignalNet returns 0 signals AND there are no leads in the database,
      synthetic fallback signals are generated for demo purposes.
      Synthetic signals are clearly marked with source="synthetic".
    
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
    
    if signals_from_pipeline == 0:
        leads = session.exec(select(Lead).where(Lead.status != "dead").limit(1)).all()
        
        if not leads:
            print("[SIGNALS] No signals from SignalNet AND no leads in DB - triggering synthetic fallback")
            synthetic_result = _generate_synthetic_fallback_signals(session, max_signals)
            
            return {
                "signals_created": synthetic_result.get("signals_created", 0),
                "events_created": synthetic_result.get("events_created", 0),
                "mode": SIGNAL_MODE,
                "source": "synthetic_fallback",
                "signalnet_result": pipeline_result,
                "message": "Used synthetic fallback (no signals from SignalNet, no leads in DB)"
            }
        else:
            print("[SIGNALS] No signals from SignalNet, but leads exist - skipping synthetic fallback")
    
    print(f"[SIGNALS] Cycle complete. Mode: {SIGNAL_MODE}, Signals: {signals_from_pipeline}, Events: {events_from_pipeline}")
    
    return {
        "signals_created": signals_from_pipeline,
        "events_created": events_from_pipeline,
        "mode": SIGNAL_MODE,
        "source": "signalnet",
        "signalnet_result": pipeline_result,
        "message": f"SignalNet pipeline completed in {SIGNAL_MODE} mode"
    }


def get_todays_opportunities(session: Session, company_id: Optional[int] = None, limit: int = 10) -> Sequence[LeadEvent]:
    """
    Get today's opportunities (LeadEvents) for display in customer portal.
    
    Sorted by urgency_score desc, then by created_at desc.
    """
    query = select(LeadEvent).order_by(
        LeadEvent.urgency_score.desc(),
        LeadEvent.created_at.desc()
    ).limit(limit)
    
    if company_id:
        query = query.where(LeadEvent.company_id == company_id)
    
    return session.exec(query).all()


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
