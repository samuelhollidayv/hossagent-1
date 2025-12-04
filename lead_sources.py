"""
Lead Source System for HossAgent.
Provides pluggable lead generation from various sources.

Configuration via environment variables:
  RELEASE_MODE - SANDBOX (default) or PRODUCTION
    - SANDBOX: Uses DummySeedLeadSourceProvider for dev/demo
    - PRODUCTION: Uses real lead sources if configured
  
  LEAD_NICHE - Target ICP description (e.g., "small B2B marketing agencies")
  LEAD_GEOGRAPHY - Optional region constraint (e.g., "US & Canada")
  LEAD_MIN_COMPANY_SIZE - Optional minimum employee count
  LEAD_MAX_COMPANY_SIZE - Optional maximum employee count
  MAX_NEW_LEADS_PER_CYCLE - Cap on leads generated per cycle (default: 10)

Provider selection (when RELEASE_MODE=PRODUCTION):
  If LEAD_SEARCH_API_URL + LEAD_SEARCH_API_KEY are set → SearchApiLeadSourceProvider
  Otherwise → Falls back to DummySeedLeadSourceProvider with warning

When RELEASE_MODE=SANDBOX (default):
  Always uses DummySeedLeadSourceProvider regardless of API credentials
"""
import os
import json
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from release_mode import ReleaseMode, get_release_mode, get_release_mode_status


class LeadSourceConfig(BaseModel):
    """Configuration for lead source targeting."""
    niche: str = "small B2B agencies doing recurring service work"
    geography: Optional[str] = None
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
    source: str = "unknown"
    raw_data: Optional[Dict[str, Any]] = None


def get_lead_source_config() -> LeadSourceConfig:
    """
    Build LeadSourceConfig from environment variables.
    Falls back to sensible defaults if not configured.
    """
    config = LeadSourceConfig(
        niche=os.getenv("LEAD_NICHE", "small B2B agencies doing recurring service work"),
        geography=os.getenv("LEAD_GEOGRAPHY"),
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


class DummySeedLeadSourceProvider(LeadSourceProvider):
    """
    Development/demo provider that generates realistic fake leads.
    Used when no external API is configured.
    """
    
    @property
    def name(self) -> str:
        return "DummySeed"
    
    SAMPLE_COMPANIES = [
        {"company": "Meridian Marketing Co", "contact": "Sarah Chen", "domain": "meridianmktg.com", "niche": "Marketing Strategy"},
        {"company": "Apex Revenue Partners", "contact": "James Wilson", "domain": "apexrevenue.io", "niche": "RevOps"},
        {"company": "Catalyst Growth Agency", "contact": "Emily Rodriguez", "domain": "catalystgrowth.co", "niche": "Growth Marketing"},
        {"company": "Vanguard Consulting Group", "contact": "Michael Foster", "domain": "vanguardcg.com", "niche": "Business Consulting"},
        {"company": "Summit Digital Solutions", "contact": "Amanda Price", "domain": "summitdigital.io", "niche": "Digital Marketing"},
        {"company": "Forge Strategic Partners", "contact": "David Kim", "domain": "forgestrategic.com", "niche": "Strategy Consulting"},
        {"company": "Horizon Brand Studio", "contact": "Rachel Thompson", "domain": "horizonbrand.co", "niche": "Branding"},
        {"company": "Elevate Agency Group", "contact": "Chris Martinez", "domain": "elevateagency.io", "niche": "Full-Service Agency"},
        {"company": "Quantum Lead Systems", "contact": "Victoria Nash", "domain": "quantumleads.com", "niche": "Lead Generation"},
        {"company": "Atlas Revenue Lab", "contact": "Alex Morgan", "domain": "atlasrevenue.co", "niche": "Revenue Operations"},
        {"company": "Pioneer Growth Co", "contact": "Jordan Lee", "domain": "pioneergrowth.io", "niche": "Growth Strategy"},
        {"company": "Keystone Advisory", "contact": "Nicole Baker", "domain": "keystoneadv.com", "niche": "Advisory Services"},
        {"company": "Momentum Marketing", "contact": "Ryan Cooper", "domain": "momentummktg.co", "niche": "Marketing"},
        {"company": "Precision Demand Gen", "contact": "Laura White", "domain": "precisiondemand.io", "niche": "Demand Generation"},
        {"company": "Sterling Strategy Group", "contact": "Daniel Harris", "domain": "sterlingstrategy.com", "niche": "Strategy"},
    ]
    
    def fetch_candidates(self, config: LeadSourceConfig, limit: int) -> List[LeadCandidate]:
        """Generate fake but realistic lead candidates."""
        available = list(self.SAMPLE_COMPANIES)
        random.shuffle(available)
        
        candidates = []
        for company_data in available[:limit]:
            candidates.append(LeadCandidate(
                company_name=company_data["company"],
                contact_name=company_data["contact"],
                email=f"{company_data['contact'].split()[0].lower()}@{company_data['domain']}",
                website=f"https://{company_data['domain']}",
                niche=company_data["niche"],
                source="dummy_seed",
                raw_data={
                    "config_niche": config.niche,
                    "config_geography": config.geography,
                    "generated_at": datetime.utcnow().isoformat()
                }
            ))
        
        print(f"[LEADS][SOURCE] DummySeed generated {len(candidates)} candidates (niche={config.niche})")
        return candidates


class ApolloLeadSourceProvider(LeadSourceProvider):
    """
    Production provider that fetches leads from Apollo.io API.
    
    Uses the apollo_integration module for:
    - Rate limiting (100 calls/day)
    - Detailed fetch logging
    - Automatic fallback when quota hit
    
    Configure via:
        - Admin console "Connect Apollo" button (preferred)
        - APOLLO_API_KEY environment variable (fallback)
    """
    
    last_error: Optional[str] = None
    last_status: str = "unknown"
    fallback_triggered: bool = False
    
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
        """Fetch leads from Apollo.io with rate limiting and auto-fallback."""
        try:
            from apollo_integration import fetch_leads_from_apollo, get_apollo_status
            
            status = get_apollo_status()
            if not status.get("connected") and not os.getenv("APOLLO_API_KEY"):
                self.last_status = "not_connected"
                self.last_error = "Apollo not connected - use admin console to connect"
                print(f"[LEADS][APOLLO] {self.last_error}")
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
            
            if result.get("fallback_to_dummy"):
                self.fallback_triggered = True
                self.last_status = "fallback"
                self.last_error = result.get("error", "Quota exceeded or API error")
                print(f"[LEADS][APOLLO] Fallback triggered: {self.last_error}")
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
            self.fallback_triggered = False
            
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


class SearchApiLeadSourceProvider(LeadSourceProvider):
    """
    Generic production provider for custom lead search APIs.
    
    Configure via environment variables:
        LEAD_SEARCH_API_URL - API endpoint
        LEAD_SEARCH_API_KEY - Authentication key
        
    Safety:
        Falls back gracefully on errors with [LEADS][API_ERROR] logging.
        Never crashes the autopilot loop.
    """
    
    last_error: Optional[str] = None
    last_status: str = "unknown"
    
    @property
    def name(self) -> str:
        return "SearchApi"
    
    def fetch_candidates(self, config: LeadSourceConfig, limit: int) -> List[LeadCandidate]:
        """Fetch leads from external search API."""
        try:
            import requests
            
            api_url = os.getenv("LEAD_SEARCH_API_URL", "")
            api_key = os.getenv("LEAD_SEARCH_API_KEY", "")
            
            if not api_url or not api_key:
                self.last_status = "no_creds"
                self.last_error = "LEAD_SEARCH_API_URL or LEAD_SEARCH_API_KEY not set"
                print(f"[LEADS][API_ERROR] {self.last_error} - falling back to zero leads")
                return []
            
            query = self._build_query(config)
            
            response = requests.get(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                params={
                    "query": query,
                    "limit": limit
                },
                timeout=30
            )
            
            if response.status_code != 200:
                self.last_status = "api_error"
                self.last_error = f"HTTP {response.status_code}: {response.text[:100]}"
                print(f"[LEADS][API_ERROR] SearchApi returned status {response.status_code} - {response.text[:200]}")
                return []
            
            data = response.json()
            
            if isinstance(data, dict) and "results" in data:
                results = data["results"]
            elif isinstance(data, list):
                results = data
            else:
                self.last_status = "api_error"
                self.last_error = "Unexpected response format"
                print(f"[LEADS][API_ERROR] SearchApi returned unexpected format - falling back to zero leads")
                return []
            
            candidates = []
            for item in results[:limit]:
                candidates.append(LeadCandidate(
                    company_name=item.get("company_name") or item.get("company", "Unknown"),
                    contact_name=item.get("contact_name") or item.get("contact"),
                    email=item.get("email"),
                    website=item.get("website") or item.get("url"),
                    niche=item.get("niche") or item.get("industry") or config.niche,
                    source="search_api",
                    raw_data=item
                ))
            
            self.last_status = "ok"
            self.last_error = None
            print(f"[LEADS][SOURCE] SearchApi fetched {len(candidates)} candidates (query={query[:50]}...)")
            return candidates
            
        except ImportError:
            self.last_status = "api_error"
            self.last_error = "'requests' library not available"
            print(f"[LEADS][API_ERROR] {self.last_error}")
            return []
        except Exception as e:
            self.last_status = "api_error"
            self.last_error = str(e)
            print(f"[LEADS][API_ERROR] Exception: {self.last_error}")
            return []
    
    def _build_query(self, config: LeadSourceConfig) -> str:
        """Build search query from config."""
        parts = [config.niche]
        
        if config.geography:
            parts.append(f"in {config.geography}")
        
        if config.min_company_size and config.max_company_size:
            parts.append(f"{config.min_company_size}-{config.max_company_size} employees")
        elif config.min_company_size:
            parts.append(f">{config.min_company_size} employees")
        elif config.max_company_size:
            parts.append(f"<{config.max_company_size} employees")
        
        return " ".join(parts)


_cached_lead_source: Optional[str] = None

def set_lead_source_preference(source: str) -> bool:
    """
    Set preferred lead source. Stored in memory for current session.
    
    Args:
        source: "apollo", "dummy", or "auto"
        
    Returns:
        True if valid source set
    """
    global _cached_lead_source
    if source in ("apollo", "dummy", "auto"):
        _cached_lead_source = source
        print(f"[LEADS] Lead source preference set to: {source}")
        return True
    return False


def get_lead_source_preference() -> str:
    """Get current lead source preference."""
    global _cached_lead_source
    return _cached_lead_source or "auto"


def get_lead_source_provider(force_fallback: bool = False) -> LeadSourceProvider:
    """
    Factory function to get the appropriate lead source provider.
    
    Provider selection:
    - If preference is "dummy": Always use DummySeed
    - If preference is "apollo": Try Apollo, fallback to DummySeed if error
    - If preference is "auto" (default):
        - SANDBOX mode: DummySeed
        - PRODUCTION mode: Apollo > SearchApi > DummySeed
    
    Auto-fallback: If Apollo quota is exceeded or errors, falls back to DummySeed
    """
    global _cached_lead_source
    preference = _cached_lead_source or "auto"
    release_mode = get_release_mode()
    
    if force_fallback or preference == "dummy":
        print("[LEADS][STARTUP] Using DummySeed provider (forced/preference)")
        return DummySeedLeadSourceProvider()
    
    if release_mode == ReleaseMode.SANDBOX and preference == "auto":
        print("[LEADS][STARTUP] Using DummySeed provider (sandbox mode)")
        return DummySeedLeadSourceProvider()
    
    if preference == "apollo" or preference == "auto":
        try:
            from apollo_integration import get_apollo_status
            status = get_apollo_status()
            
            if status.get("connected") or os.getenv("APOLLO_API_KEY"):
                if status.get("rate_limit_ok", True):
                    print(f"[LEADS][STARTUP] Using Apollo.io provider (production mode, {status.get('calls_today', 0)}/100 calls today)")
                    return ApolloLeadSourceProvider()
                else:
                    print(f"[LEADS][STARTUP][WARNING] Apollo quota exceeded - using DummySeed fallback")
                    return DummySeedLeadSourceProvider()
        except ImportError:
            pass
    
    if os.getenv("LEAD_SEARCH_API_URL") and os.getenv("LEAD_SEARCH_API_KEY"):
        print("[LEADS][STARTUP] Using SearchApi provider (production mode)")
        return SearchApiLeadSourceProvider()
    
    print("[LEADS][STARTUP][WARNING] PRODUCTION mode but no LEAD_SEARCH_API_KEY - using DummySeed fallback")
    return DummySeedLeadSourceProvider()


def get_lead_source_status() -> Dict[str, Any]:
    """
    Get current lead source configuration status for admin display.
    
    Returns dict with config values, provider info, release mode, and last run status.
    """
    config = get_lead_source_config()
    provider = get_lead_source_provider()
    release_status = get_release_mode_status()
    preference = get_lead_source_preference()
    
    is_real_provider = isinstance(provider, (ApolloLeadSourceProvider, SearchApiLeadSourceProvider))
    
    apollo_status = {}
    try:
        from apollo_integration import get_apollo_status
        apollo_status = get_apollo_status()
    except ImportError:
        apollo_status = {"connected": False, "error": "Module not loaded"}
    
    status = {
        "niche": config.niche,
        "geography": config.geography,
        "min_company_size": config.min_company_size,
        "max_company_size": config.max_company_size,
        "max_new_leads_per_cycle": config.max_new_leads_per_cycle,
        "provider": provider.name,
        "provider_configured": is_real_provider,
        "release_mode": release_status["mode"],
        "release_mode_message": release_status["message"],
        "preference": preference,
        "apollo": apollo_status,
        "last_status": "ok",
        "last_error": None
    }
    
    if is_real_provider:
        status["last_status"] = getattr(provider, "last_status", "unknown")
        status["last_error"] = getattr(provider, "last_error", None)
        if hasattr(provider, "fallback_triggered"):
            status["fallback_triggered"] = provider.fallback_triggered
    
    return status
