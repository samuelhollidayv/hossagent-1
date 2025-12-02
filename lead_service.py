"""
Lead Service for HossAgent.
Handles lead generation, deduplication, and management.
"""
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from sqlmodel import Session, select
from models import Lead
from lead_sources import (
    get_lead_source_config,
    get_lead_source_provider,
    LeadCandidate,
    LeadSourceConfig
)


LEAD_SOURCE_LOG_FILE = Path("lead_source_log.json")
MAX_LOG_ENTRIES = 100


def _load_lead_source_log() -> Dict[str, Any]:
    """Load lead source run log."""
    try:
        if LEAD_SOURCE_LOG_FILE.exists():
            with open(LEAD_SOURCE_LOG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"runs": [], "last_run": None, "last_created_count": 0}


def _save_lead_source_log(log_data: Dict[str, Any]) -> None:
    """Save lead source run log."""
    try:
        if "runs" in log_data:
            log_data["runs"] = log_data["runs"][-MAX_LOG_ENTRIES:]
        with open(LEAD_SOURCE_LOG_FILE, "w") as f:
            json.dump(log_data, f, indent=2)
    except Exception as e:
        print(f"[LEADS] Warning: Could not save lead source log: {e}")


def log_lead_source_run(
    provider: str,
    niche: str,
    geography: Optional[str],
    candidates_fetched: int,
    leads_created: int,
    leads_skipped: int
) -> None:
    """Log a lead source run for admin visibility."""
    log_data = _load_lead_source_log()
    
    run_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "provider": provider,
        "niche": niche,
        "geography": geography,
        "candidates_fetched": candidates_fetched,
        "leads_created": leads_created,
        "leads_skipped": leads_skipped
    }
    
    log_data["runs"].append(run_entry)
    log_data["last_run"] = run_entry["timestamp"]
    log_data["last_created_count"] = leads_created
    
    _save_lead_source_log(log_data)


def get_lead_source_log() -> Dict[str, Any]:
    """Get lead source run log for admin display."""
    return _load_lead_source_log()


def get_recent_auto_leads(limit: int = 10) -> List[Dict[str, Any]]:
    """Get most recent auto-created leads for admin display."""
    log_data = _load_lead_source_log()
    return log_data.get("recent_leads", [])[-limit:]


def _lead_exists(session: Session, email: str, company: str, website: Optional[str] = None) -> bool:
    """
    Check if a similar lead already exists.
    Matches by email (primary) or by company+website combo.
    """
    if email:
        existing = session.exec(
            select(Lead).where(Lead.email == email)
        ).first()
        if existing:
            return True
    
    if website and company:
        existing = session.exec(
            select(Lead).where(
                (Lead.company == company) & (Lead.website == website)
            )
        ).first()
        if existing:
            return True
    
    return False


def generate_new_leads_from_source(session: Session) -> str:
    """
    Main entry point for auto-generating leads from configured source.
    
    This function:
    1. Gets lead source config from environment
    2. Selects appropriate provider (SearchApi or DummySeed)
    3. Fetches candidates up to max_new_leads_per_cycle
    4. Deduplicates against existing leads
    5. Creates new Lead records with status="new"
    6. Logs the run for admin visibility
    
    Returns:
        Status message describing what was done
    """
    config = get_lead_source_config()
    provider = get_lead_source_provider()
    
    print(f"[LEADS][SOURCE] Starting lead generation (provider={provider.name}, max={config.max_new_leads_per_cycle})")
    
    candidates = provider.fetch_candidates(config, limit=config.max_new_leads_per_cycle)
    
    if not candidates:
        msg = f"LeadSource: No candidates from {provider.name}"
        print(f"[LEADS][SOURCE] {msg}")
        log_lead_source_run(
            provider=provider.name,
            niche=config.niche,
            geography=config.geography,
            candidates_fetched=0,
            leads_created=0,
            leads_skipped=0
        )
        return msg
    
    leads_created = 0
    leads_skipped = 0
    created_leads = []
    
    for candidate in candidates:
        if not candidate.email and not candidate.website:
            print(f"[LEADS][SOURCE] Skipping {candidate.company_name}: no email or website")
            leads_skipped += 1
            continue
        
        if _lead_exists(session, candidate.email or "", candidate.company_name, candidate.website):
            print(f"[LEADS][SOURCE] Skipping {candidate.company_name}: already exists")
            leads_skipped += 1
            continue
        
        lead = Lead(
            name=candidate.contact_name or "Contact",
            email=candidate.email or f"info@{candidate.company_name.lower().replace(' ', '')}.com",
            company=candidate.company_name,
            niche=candidate.niche or config.niche,
            status="new",
            website=candidate.website,
            source=candidate.source
        )
        session.add(lead)
        session.flush()
        
        created_leads.append({
            "id": lead.id,
            "company": lead.company,
            "email": lead.email,
            "source": lead.source,
            "created_at": datetime.utcnow().isoformat()
        })
        
        leads_created += 1
        print(f"[LEADS][SOURCE] Created lead: {lead.company} ({lead.email})")
    
    session.commit()
    
    log_data = _load_lead_source_log()
    if "recent_leads" not in log_data:
        log_data["recent_leads"] = []
    log_data["recent_leads"].extend(created_leads)
    log_data["recent_leads"] = log_data["recent_leads"][-50:]
    _save_lead_source_log(log_data)
    
    log_lead_source_run(
        provider=provider.name,
        niche=config.niche,
        geography=config.geography,
        candidates_fetched=len(candidates),
        leads_created=leads_created,
        leads_skipped=leads_skipped
    )
    
    msg = f"LeadSource: Created {leads_created} new leads, skipped {leads_skipped} (provider={provider.name}, niche=\"{config.niche[:30]}...\")"
    print(f"[LEADS][SOURCE] {msg}")
    return msg
