"""
Lead Service for HossAgent.
Handles lead generation, deduplication, and management.

Deduplication rules:
- Primary: Match by email address
- Secondary: Match by (company_name + normalized_domain)
- Domain normalization: strips www., http://, https://, trailing paths

Logs all dedupe events with [LEADS][DEDUPED] tag.
"""
import json
import re
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
MAX_LOG_ENTRIES = 5000  # Capped to prevent unbounded growth


def _normalize_domain(website: Optional[str]) -> Optional[str]:
    """
    Normalize a website URL to a domain for deduplication.
    
    Examples:
        https://www.example.com/page -> example.com
        http://example.com -> example.com
        www.example.com -> example.com
    """
    if not website:
        return None
    
    domain = website.lower().strip()
    domain = re.sub(r'^https?://', '', domain)
    domain = re.sub(r'^www\.', '', domain)
    domain = domain.split('/')[0]
    domain = domain.split('?')[0]
    
    return domain if domain else None


def _normalize_company(company: str) -> str:
    """Normalize company name for comparison."""
    return company.lower().strip()


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


def _lead_exists(session: Session, email: str, company: str, website: Optional[str] = None) -> Optional[int]:
    """
    Check if a similar lead already exists.
    
    Matches by:
    1. Email (primary) - exact match, case-insensitive
    2. Company + Domain (secondary) - normalized domain comparison
    
    Returns:
        Lead ID if exists, None otherwise
    """
    if email:
        email_lower = email.lower().strip()
        existing = session.exec(
            select(Lead).where(Lead.email == email_lower)
        ).first()
        if existing and existing.status != "invalid":
            return existing.id
    
    if website and company:
        domain = _normalize_domain(website)
        company_norm = _normalize_company(company)
        
        if domain:
            all_leads = session.exec(select(Lead).limit(1000)).all()
            for lead in all_leads:
                if lead.status == "invalid":
                    continue
                lead_domain = _normalize_domain(lead.website)
                lead_company = _normalize_company(lead.company)
                if lead_domain == domain and lead_company == company_norm:
                    return lead.id
    
    return None


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
    leads_deduped = 0
    created_leads = []
    
    for candidate in candidates:
        if not candidate.email and not candidate.website:
            print(f"[LEADS][SOURCE] Skipping {candidate.company_name}: no email or website")
            leads_skipped += 1
            continue
        
        existing_id = _lead_exists(session, candidate.email or "", candidate.company_name, candidate.website)
        if existing_id is not None:
            print(f"[LEADS][DEDUPED] {candidate.company_name}: matches existing lead {existing_id}")
            leads_deduped += 1
            leads_skipped += 1
            continue
        
        email_to_use = candidate.email
        if email_to_use:
            email_to_use = email_to_use.lower().strip()
        else:
            email_to_use = f"info@{candidate.company_name.lower().replace(' ', '')}.com"
        
        lead = Lead(
            name=candidate.contact_name or "Contact",
            email=email_to_use,
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
