"""
Signals Agent - The Ethical Briefcase System

This agent monitors external context signals about companies and generates
actionable LeadEvents for moment-aware outreach. It transforms HossAgent
from generic lead gen into a context-aware intelligence engine.

Miami-tuned heuristics included for South Florida market.
"""

import json
import random
import os
from datetime import datetime
from typing import Optional, Dict, Sequence
from sqlmodel import Session, select

from models import Signal, LeadEvent, Customer, Lead


MIAMI_INDUSTRIES = [
    "med spa", "hvac", "roofing", "immigration attorney", 
    "realtor", "insurance broker", "marketing agency",
    "dental practice", "auto repair", "landscaping"
]

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


def infer_category(signal_type: str, context: str) -> str:
    """Infer LeadEvent category from signal content."""
    context_lower = context.lower()
    
    if "hurricane" in context_lower or "storm" in context_lower:
        return "HURRICANE_SEASON"
    elif "competitor" in context_lower or "pricing" in context_lower:
        return "COMPETITOR_SHIFT"
    elif "hiring" in context_lower or "job" in context_lower or "growth" in context_lower:
        return "GROWTH_SIGNAL"
    elif "bilingual" in context_lower or "spanish" in context_lower:
        return "BILINGUAL_OPPORTUNITY"
    elif "review" in context_lower:
        return "REPUTATION_CHANGE"
    elif "price" in context_lower or "pricing" in context_lower:
        return "MIAMI_PRICE_MOVE"
    else:
        return "OPPORTUNITY"


def calculate_urgency(signal_type: str, category: str) -> int:
    """Calculate urgency score 0-100 based on signal characteristics."""
    base_score = 50
    
    if category == "HURRICANE_SEASON":
        base_score = 75
    elif category == "COMPETITOR_SHIFT":
        base_score = 65
    elif category == "GROWTH_SIGNAL":
        base_score = 60
    elif category == "BILINGUAL_OPPORTUNITY":
        base_score = 55
    elif category == "REPUTATION_CHANGE":
        base_score = 70
    
    variation = random.randint(-10, 10)
    return max(30, min(90, base_score + variation))


def generate_recommended_action(category: str, signal_summary: str) -> str:
    """Generate recommended action based on category."""
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
    Run the Signals Agent to generate synthetic context signals.
    
    This v1 is intentionally synthetic - it simulates the 'obituary men' 
    noticing engine without hitting real APIs. Each signal generates
    actionable LeadEvents for moment-aware outreach.
    
    Returns dict with counts of signals and events generated.
    """
    print("[SIGNALS] Starting Signals Agent cycle...")
    
    customers = session.exec(select(Customer).limit(20)).all()
    leads = session.exec(select(Lead).where(Lead.status != "dead").limit(30)).all()
    
    all_companies = []
    for c in customers:
        all_companies.append({
            "id": c.id,
            "name": c.company,
            "niche": c.niche or "small business",
            "type": "customer"
        })
    for l in leads:
        all_companies.append({
            "id": l.id,
            "name": l.company,
            "niche": l.niche or "small business",
            "type": "lead"
        })
    
    if not all_companies:
        print("[SIGNALS] No companies found. Skipping signal generation.")
        return {"signals_created": 0, "events_created": 0}
    
    signals_created = 0
    events_created = 0
    
    companies_to_process = random.sample(all_companies, min(len(all_companies), max_signals))
    
    for company in companies_to_process:
        num_signals = random.randint(0, 3)
        if num_signals == 0:
            continue
            
        signal_generators = [
            generate_competitor_signal,
            generate_job_posting_signal,
            generate_review_signal,
            generate_local_signal
        ]
        
        chosen_generators = random.sample(signal_generators, min(num_signals, len(signal_generators)))
        
        for generator in chosen_generators:
            signal_data = generator(company["name"], company["niche"])
            
            signal = Signal(
                company_id=company["id"] if company["type"] == "customer" else None,
                lead_id=company["id"] if company["type"] == "lead" else None,
                source_type=signal_data["source_type"],
                raw_payload=signal_data["raw_payload"],
                context_summary=signal_data["context_summary"],
                geography=random.choice(MIAMI_AREAS)
            )
            session.add(signal)
            session.commit()
            session.refresh(signal)
            signals_created += 1
            
            print(f"[SIGNALS][{signal.source_type.upper()}] {company['name']}: {signal.context_summary[:80]}...")
            
            category = infer_category(signal.source_type, signal.context_summary)
            urgency = calculate_urgency(signal.source_type, category)
            recommended_action = generate_recommended_action(category, signal.context_summary)
            
            event = LeadEvent(
                company_id=company["id"] if company["type"] == "customer" else None,
                lead_id=company["id"] if company["type"] == "lead" else None,
                signal_id=signal.id,
                summary=signal.context_summary,
                category=category,
                urgency_score=urgency,
                status="new",
                recommended_action=recommended_action
            )
            session.add(event)
            session.commit()
            events_created += 1
            
            print(f"[SIGNALS][EVENT] Created {category} event (urgency: {urgency}) for {company['name']}")
    
    print(f"[SIGNALS] Cycle complete. Created {signals_created} signals, {events_created} events.")
    
    return {
        "signals_created": signals_created,
        "events_created": events_created
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
