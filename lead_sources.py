"""
Lead Source System for HossAgent - HossNative ONLY.

HossNative is the ONLY lead source - autonomous discovery without 3rd-party APIs.
No Apollo. No Hunter. No Clearbit. Just HossAgent's native intelligence.

Configuration via environment variables:
  LEAD_NICHE - Target ICP description (e.g., "med spa, HVAC, realtor")
  LEAD_GEOGRAPHY - Region constraint (default: "Miami, Broward, South Florida")
  MAX_NEW_LEADS_PER_CYCLE - Cap on leads generated per cycle (default: 10)

Lead discovery flow:
  1. SignalNet identifies business signals (news, reviews, events)
  2. HossNative resolves company domains from signals
  3. HossNative scrapes websites to find contact emails
  4. Verified leads become LeadEvents ready for outbound
"""
import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel


class LeadSourceConfig(BaseModel):
    """Configuration for lead source targeting."""
    niche: str = "med spa, HVAC, realtor, roofing, immigration attorney, marketing agency"
    geography: Optional[str] = "Miami, Broward, South Florida"
    max_new_leads_per_cycle: int = 10


class LeadCandidate(BaseModel):
    """A potential lead discovered by HossNative."""
    company_name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    niche: Optional[str] = None
    source: str = "HossNative"
    raw_data: Optional[Dict[str, Any]] = None


def get_lead_source_config() -> LeadSourceConfig:
    """
    Build LeadSourceConfig from environment variables.
    Falls back to sensible defaults if not configured.
    """
    config = LeadSourceConfig(
        niche=os.getenv("LEAD_NICHE", "med spa, HVAC, realtor, roofing, immigration attorney, marketing agency"),
        geography=os.getenv("LEAD_GEOGRAPHY", "Miami, Broward, South Florida"),
        max_new_leads_per_cycle=int(os.getenv("MAX_NEW_LEADS_PER_CYCLE", "10"))
    )
    return config


class HossNativeProvider:
    """
    HossNative Lead Discovery Provider.
    
    The ONLY lead source for HossAgent - autonomous discovery using:
    - SignalNet for business signals and opportunities
    - Web scraping for email discovery
    - Domain resolution from company names
    - Pattern-based email validation
    
    No external APIs. No paid enrichment. Just native intelligence.
    """
    
    name: str = "HossNative"
    last_error: Optional[str] = None
    last_status: str = "ready"
    
    def fetch_candidates(self, config: LeadSourceConfig, limit: int) -> List[LeadCandidate]:
        """
        Fetch lead candidates from HossNative discovery.
        
        HossNative doesn't "fetch" candidates like an API - instead it:
        1. Uses SignalNet to identify business signals
        2. Extracts company information from those signals
        3. Resolves domains and scrapes for contact emails
        
        The actual discovery happens through the enrichment pipeline.
        This method returns empty since leads come from SignalNet events.
        """
        print("[LEADS][HOSSNATIVE] Using SignalNet for autonomous lead discovery")
        print(f"[LEADS][HOSSNATIVE] Geography: {config.geography}, Niche: {config.niche[:50]}...")
        
        self.last_status = "active"
        self.last_error = None
        
        return []


def get_lead_source_provider() -> HossNativeProvider:
    """
    Get the HossNative lead source provider.
    
    HossNative is the ONLY provider - autonomous discovery with no external APIs.
    Leads come from SignalNet events, not from traditional API-based lead sources.
    """
    print("[LEADS][STARTUP] HossNative (Autonomous Discovery) active")
    print("[LEADS][STARTUP] Lead discovery via SignalNet + web scraping")
    return HossNativeProvider()


def get_lead_source_status() -> Dict[str, Any]:
    """
    Get current lead source status for admin display.
    
    Returns HossNative status and configuration.
    """
    config = get_lead_source_config()
    
    from release_mode import get_release_mode_status
    release_status = get_release_mode_status()
    
    status = {
        "niche": config.niche,
        "geography": config.geography,
        "max_new_leads_per_cycle": config.max_new_leads_per_cycle,
        "provider": "HossNative",
        "provider_type": "autonomous_discovery",
        "provider_configured": True,
        "provider_ready": True,
        "release_mode": release_status["mode"],
        "release_mode_message": release_status["message"],
        "last_status": "active",
        "last_error": None,
        "discovery_method": "SignalNet + Web Scraping",
        "external_apis": "None - fully autonomous"
    }
    
    return status


_lead_source_log: Dict[str, Any] = {
    "runs": [],
    "last_run": None,
    "last_created_count": 0,
    "recent_leads": []
}


def log_lead_source_run(created: int, skipped: int, provider: str, error: Optional[str] = None):
    """Log a lead source run for admin visibility."""
    global _lead_source_log
    
    run_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "provider": provider,
        "created": created,
        "skipped": skipped,
        "error": error
    }
    
    _lead_source_log["runs"].append(run_entry)
    _lead_source_log["runs"] = _lead_source_log["runs"][-50:]
    _lead_source_log["last_run"] = run_entry["timestamp"]
    _lead_source_log["last_created_count"] = created


def log_lead_created(lead_name: str, email: str, source: str):
    """Log a newly created lead."""
    global _lead_source_log
    
    _lead_source_log["recent_leads"].append({
        "name": lead_name,
        "email": email,
        "source": source,
        "created_at": datetime.utcnow().isoformat()
    })
    _lead_source_log["recent_leads"] = _lead_source_log["recent_leads"][-20:]


def get_lead_source_log() -> Dict[str, Any]:
    """Get the lead source run log."""
    return _lead_source_log
