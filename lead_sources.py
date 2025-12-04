"""
Lead Source System for HossAgent.
Provides lead generation from Apollo.io - the ONLY lead source.

Configuration via environment variables:
  LEAD_NICHE - Target ICP description (e.g., "med spa, HVAC, realtor")
  LEAD_GEOGRAPHY - Region constraint (default: "Miami, Broward, South Florida")
  LEAD_MIN_COMPANY_SIZE - Optional minimum employee count
  LEAD_MAX_COMPANY_SIZE - Optional maximum employee count
  MAX_NEW_LEADS_PER_CYCLE - Cap on leads generated per cycle (default: 10)

Apollo.io is the ONLY lead source. Configure via:
  - Admin console "Connect Apollo" button (preferred)
  - APOLLO_API_KEY environment variable
  
If Apollo is not connected, lead generation will pause (not fall back).
"""
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from release_mode import get_release_mode_status


class LeadSourceConfig(BaseModel):
    """Configuration for lead source targeting."""
    niche: str = "med spa, HVAC, realtor, roofing, immigration attorney, marketing agency"
    geography: Optional[str] = "Miami, Broward, South Florida"
    min_company_size: Optional[int] = None
    max_company_size: Optional[int] = None
    max_new_leads_per_cycle: int = 10


class LeadCandidate(BaseModel):
    """A potential lead discovered by a lead source provider."""
    company_name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    niche: Optional[str] = None
    source: str = "apollo"
    raw_data: Optional[Dict[str, Any]] = None


def get_lead_source_config() -> LeadSourceConfig:
    """
    Build LeadSourceConfig from environment variables.
    Falls back to sensible defaults if not configured.
    """
    config = LeadSourceConfig(
        niche=os.getenv("LEAD_NICHE", "med spa, HVAC, realtor, roofing, immigration attorney, marketing agency"),
        geography=os.getenv("LEAD_GEOGRAPHY", "Miami, Broward, South Florida"),
        min_company_size=int(os.getenv("LEAD_MIN_COMPANY_SIZE")) if os.getenv("LEAD_MIN_COMPANY_SIZE") else None,
        max_company_size=int(os.getenv("LEAD_MAX_COMPANY_SIZE")) if os.getenv("LEAD_MAX_COMPANY_SIZE") else None,
        max_new_leads_per_cycle=int(os.getenv("MAX_NEW_LEADS_PER_CYCLE", "10"))
    )
    return config


class LeadSourceProvider(ABC):
    """Abstract base class for lead source providers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging and display."""
        ...
    
    @abstractmethod
    def fetch_candidates(self, config: LeadSourceConfig, limit: int) -> List[LeadCandidate]:
        """
        Fetch lead candidates based on config.
        
        Args:
            config: Lead source configuration with targeting criteria
            limit: Maximum number of candidates to return
            
        Returns:
            List of LeadCandidate objects
        """
        ...


class ApolloLeadSourceProvider(LeadSourceProvider):
    """
    Production provider that fetches leads from Apollo.io API.
    This is the ONLY lead source - no fallbacks.
    
    Uses the apollo_integration module for:
    - Rate limiting (100 calls/day)
    - Detailed fetch logging
    - Connection management
    
    Configure via:
        - Admin console "Connect Apollo" button (preferred)
        - APOLLO_API_KEY environment variable
    """
    
    last_error: Optional[str] = None
    last_status: str = "unknown"
    
    INDUSTRY_KEYWORDS = {
        "med spa": ["medical spa", "medspa", "aesthetics", "cosmetic", "beauty clinic"],
        "hvac": ["hvac", "heating", "cooling", "air conditioning", "climate control"],
        "realtor": ["real estate", "realtor", "property", "brokerage", "realty"],
        "roofing": ["roofing", "roof", "roofer", "construction"],
        "immigration attorney": ["immigration", "law firm", "attorney", "legal services"],
        "marketing agency": ["marketing", "advertising", "digital agency", "media agency"]
    }
    
    @property
    def name(self) -> str:
        return "Apollo"
    
    def fetch_candidates(self, config: LeadSourceConfig, limit: int) -> List[LeadCandidate]:
        """Fetch leads from Apollo.io. No fallback - Apollo or nothing."""
        try:
            from apollo_integration import fetch_leads_from_apollo, get_apollo_status
            
            status = get_apollo_status()
            if not status.get("connected") and not os.getenv("APOLLO_API_KEY"):
                self.last_status = "not_connected"
                self.last_error = "Apollo not connected - use admin console to connect or set APOLLO_API_KEY"
                print(f"[LEADS][APOLLO] {self.last_error}")
                print("[LEADS][APOLLO] Lead generation PAUSED until Apollo is connected")
                return []
            
            if not status.get("rate_limit_ok", True):
                self.last_status = "quota_exceeded"
                self.last_error = f"Daily quota exceeded ({status.get('calls_today', 100)}/100). Resets at midnight UTC."
                print(f"[LEADS][APOLLO] {self.last_error}")
                print("[LEADS][APOLLO] Lead generation PAUSED until quota resets")
                return []
            
            location = config.geography or "Miami"
            niche = config.niche or "small business"
            min_size = config.min_company_size or 1
            max_size = config.max_company_size or 200
            
            result = fetch_leads_from_apollo(
                location=location,
                niche=niche,
                min_size=min_size,
                max_size=max_size,
                limit=limit
            )
            
            if result.get("quota_exceeded"):
                self.last_status = "quota_exceeded"
                self.last_error = "Daily API quota exceeded"
                print(f"[LEADS][APOLLO] Quota exceeded - lead generation PAUSED")
                return []
            
            if not result.get("success"):
                self.last_status = "api_error"
                self.last_error = result.get("error", "Unknown error")
                print(f"[LEADS][APOLLO] Error: {self.last_error}")
                return []
            
            leads = result.get("leads", [])
            candidates = []
            
            for lead in leads:
                website = lead.get("website", "")
                if website and not website.startswith("http"):
                    website = f"https://{website}"
                
                candidates.append(LeadCandidate(
                    company_name=lead.get("company", "Unknown"),
                    contact_name=lead.get("name"),
                    email=lead.get("email"),
                    website=website,
                    niche=lead.get("industry", config.niche),
                    source="apollo",
                    raw_data={
                        "apollo_id": lead.get("apollo_id"),
                        "apollo_url": lead.get("apollo_url"),
                        "title": lead.get("title"),
                        "linkedin": lead.get("linkedin"),
                        "city": lead.get("city"),
                        "state": lead.get("state"),
                        "employee_count": lead.get("employee_count")
                    }
                ))
            
            self.last_status = "ok"
            self.last_error = None
            
            calls_remaining = result.get("calls_remaining", "?")
            print(f"[LEADS][APOLLO] Fetched {len(candidates)} real leads ({calls_remaining} API calls remaining today)")
            
            return candidates
            
        except ImportError:
            self.last_status = "module_error"
            self.last_error = "apollo_integration module not found"
            print(f"[LEADS][APOLLO] {self.last_error}")
            return []
        except Exception as e:
            self.last_status = "exception"
            self.last_error = str(e)
            print(f"[LEADS][APOLLO] Exception: {self.last_error}")
            return []


def get_lead_source_provider() -> LeadSourceProvider:
    """
    Get the Apollo lead source provider.
    Apollo is the ONLY lead source - no fallbacks, no alternatives.
    
    If Apollo is not connected, the provider will return empty results
    and log that lead generation is paused.
    """
    try:
        from apollo_integration import get_apollo_status
        status = get_apollo_status()
        
        if status.get("connected") or os.getenv("APOLLO_API_KEY"):
            calls_today = status.get("calls_today", 0)
            rate_ok = status.get("rate_limit_ok", True)
            
            if rate_ok:
                print(f"[LEADS][STARTUP] Apollo.io connected ({calls_today}/100 calls today)")
            else:
                print(f"[LEADS][STARTUP] Apollo.io connected but QUOTA EXCEEDED ({calls_today}/100)")
        else:
            print("[LEADS][STARTUP] Apollo.io NOT connected - lead generation PAUSED")
            print("[LEADS][STARTUP] Connect Apollo via admin console or set APOLLO_API_KEY")
            
    except ImportError:
        print("[LEADS][STARTUP] Apollo module not loaded - lead generation PAUSED")
    
    return ApolloLeadSourceProvider()


def get_lead_source_status() -> Dict[str, Any]:
    """
    Get current lead source configuration status for admin display.
    
    Returns dict with config values, Apollo status, and last run status.
    """
    config = get_lead_source_config()
    provider = get_lead_source_provider()
    release_status = get_release_mode_status()
    
    apollo_status = {}
    try:
        from apollo_integration import get_apollo_status
        apollo_status = get_apollo_status()
    except ImportError:
        apollo_status = {"connected": False, "error": "Module not loaded"}
    
    is_connected = apollo_status.get("connected") or bool(os.getenv("APOLLO_API_KEY"))
    is_ready = is_connected and apollo_status.get("rate_limit_ok", True)
    
    status = {
        "niche": config.niche,
        "geography": config.geography,
        "min_company_size": config.min_company_size,
        "max_company_size": config.max_company_size,
        "max_new_leads_per_cycle": config.max_new_leads_per_cycle,
        "provider": "Apollo",
        "provider_configured": is_connected,
        "provider_ready": is_ready,
        "release_mode": release_status["mode"],
        "release_mode_message": release_status["message"],
        "apollo": apollo_status,
        "last_status": getattr(provider, "last_status", "unknown"),
        "last_error": getattr(provider, "last_error", None)
    }
    
    if not is_connected:
        status["last_status"] = "not_connected"
        status["last_error"] = "Apollo not connected - connect via admin console"
    elif not apollo_status.get("rate_limit_ok", True):
        status["last_status"] = "quota_exceeded"
        status["last_error"] = f"Daily quota exceeded ({apollo_status.get('calls_today', 100)}/100)"
    
    return status


# Lead source run log (in memory)
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
