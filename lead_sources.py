"""
Lead Source System for HossAgent.
Provides pluggable lead generation from various sources.

Configuration via environment variables:
  LEAD_NICHE - Target ICP description (e.g., "small B2B marketing agencies")
  LEAD_GEOGRAPHY - Optional region constraint (e.g., "US & Canada")
  LEAD_MIN_COMPANY_SIZE - Optional minimum employee count
  LEAD_MAX_COMPANY_SIZE - Optional maximum employee count
  MAX_NEW_LEADS_PER_CYCLE - Cap on leads generated per cycle (default: 10)

Provider selection:
  If LEAD_SEARCH_API_URL + LEAD_SEARCH_API_KEY are set → SearchApiLeadSourceProvider
  Otherwise → DummySeedLeadSourceProvider (for dev/demo)
"""
import os
import json
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel


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


class SearchApiLeadSourceProvider(LeadSourceProvider):
    """
    Production provider that fetches leads from an external search/enrichment API.
    
    Expected API response format:
    [
        {
            "company_name": "Acme Corp",
            "contact_name": "John Doe",
            "email": "john@acme.com",
            "website": "https://acme.com",
            "niche": "Marketing"
        },
        ...
    ]
    
    Configure via environment variables:
        LEAD_SEARCH_API_URL - API endpoint
        LEAD_SEARCH_API_KEY - Authentication key
        
    Safety:
        Falls back gracefully on errors with [LEADS][API_ERROR] logging.
        Never crashes the autopilot loop.
    """
    
    last_error: Optional[str] = None
    last_status: str = "unknown"  # "ok", "no_creds", "api_error"
    
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


def get_lead_source_provider() -> LeadSourceProvider:
    """
    Factory function to get the appropriate lead source provider.
    
    Returns SearchApiLeadSourceProvider if API credentials are configured,
    otherwise returns DummySeedLeadSourceProvider for dev/demo.
    """
    if os.getenv("LEAD_SEARCH_API_URL") and os.getenv("LEAD_SEARCH_API_KEY"):
        return SearchApiLeadSourceProvider()
    else:
        return DummySeedLeadSourceProvider()


def get_lead_source_status() -> Dict[str, Any]:
    """
    Get current lead source configuration status for admin display.
    
    Returns dict with config values, provider info, and last run status.
    """
    config = get_lead_source_config()
    provider = get_lead_source_provider()
    
    status = {
        "niche": config.niche,
        "geography": config.geography,
        "min_company_size": config.min_company_size,
        "max_company_size": config.max_company_size,
        "max_new_leads_per_cycle": config.max_new_leads_per_cycle,
        "provider": provider.name,
        "provider_configured": isinstance(provider, SearchApiLeadSourceProvider),
        "last_status": "ok",
        "last_error": None
    }
    
    if isinstance(provider, SearchApiLeadSourceProvider):
        status["last_status"] = getattr(provider, "last_status", "unknown")
        status["last_error"] = getattr(provider, "last_error", None)
    
    return status
