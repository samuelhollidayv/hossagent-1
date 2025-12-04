"""
Apollo.io Integration Module - Hoss Style (METADATA ONLY)

Provides seamless Apollo.io integration with:
- OAuth 2.0 flow (one-click connect)
- Automatic token refresh
- Rate limiting (100 calls/day)
- Detailed fetch logging

MODE: METADATA ONLY
- People Search API is DISABLED (requires paid tier)
- Only company/organization enrichment endpoints are used
- Lead generation uses SignalNet instead of Apollo People Search
- Free tier friendly endpoints only

Metadata endpoints used:
- /v1/organizations/enrich (company data)
- /v1/auth/health (connection validation)
"""

import os
import json
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from pathlib import Path

APOLLO_FETCH_LOG = "apollo_fetch.log"
APOLLO_STATE_FILE = "apollo_state.json"
DAILY_LIMIT = 100
APOLLO_API_BASE = "https://api.apollo.io/v1"


@dataclass
class ApolloState:
    """Persistent state for Apollo integration."""
    connected: bool = False
    api_key: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_expires_at: Optional[str] = None
    calls_today: int = 0
    last_reset_date: Optional[str] = None
    last_error: Optional[str] = None
    last_fetch_at: Optional[str] = None
    total_leads_fetched: int = 0
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "ApolloState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _load_state() -> ApolloState:
    """Load Apollo state from file."""
    try:
        if Path(APOLLO_STATE_FILE).exists():
            with open(APOLLO_STATE_FILE, 'r') as f:
                data = json.load(f)
                return ApolloState.from_dict(data)
    except Exception as e:
        print(f"[APOLLO] Error loading state: {e}")
    return ApolloState()


def _save_state(state: ApolloState):
    """Save Apollo state to file."""
    try:
        with open(APOLLO_STATE_FILE, 'w') as f:
            json.dump(state.to_dict(), f, indent=2)
    except Exception as e:
        print(f"[APOLLO] Error saving state: {e}")


def _log_fetch(query_params: dict, result_count: int, success: bool, error: Optional[str] = None):
    """Log Apollo API fetch to log file."""
    try:
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "query": query_params,
            "result_count": result_count,
            "success": success,
            "error": error
        }
        with open(APOLLO_FETCH_LOG, 'a') as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[APOLLO] Log error: {e}")


def _check_rate_limit(state: ApolloState) -> tuple[bool, str]:
    """Check if we're within rate limits. Returns (allowed, message)."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    if state.last_reset_date != today:
        state.calls_today = 0
        state.last_reset_date = today
        _save_state(state)
    
    if state.calls_today >= DAILY_LIMIT:
        return False, f"Daily limit reached ({DAILY_LIMIT} calls). Resets at midnight UTC."
    
    return True, f"{DAILY_LIMIT - state.calls_today} calls remaining today"


def get_apollo_status() -> Dict[str, Any]:
    """Get current Apollo integration status for admin display."""
    state = _load_state()
    env_key = os.getenv("APOLLO_API_KEY", "").strip()
    
    allowed, rate_msg = _check_rate_limit(state)
    
    is_connected = bool(state.connected and (state.api_key or env_key or state.access_token))
    auth_method = None
    connection_source = None
    
    if state.access_token:
        auth_method = "oauth"
        connection_source = "oauth"
    elif state.api_key or env_key:
        auth_method = "api_key"
        if env_key and state.api_key == env_key:
            connection_source = "environment_secret"
        elif state.api_key:
            connection_source = "manual"
    
    return {
        "connected": is_connected,
        "mode": "metadata_only",
        "people_search_enabled": False,
        "auth_method": auth_method,
        "connection_source": connection_source,
        "calls_today": state.calls_today,
        "daily_limit": DAILY_LIMIT,
        "rate_limit_ok": allowed,
        "rate_limit_message": rate_msg,
        "last_error": state.last_error,
        "last_fetch_at": state.last_fetch_at,
        "total_leads_fetched": state.total_leads_fetched,
        "has_env_key": bool(env_key)
    }


def connect_apollo_with_key(api_key: str) -> Dict[str, Any]:
    """
    Connect Apollo using API key (validates before saving).
    
    NOTE: Metadata-only mode - validates using health endpoint only.
    Does NOT test People Search API (disabled for free tier).
    
    Args:
        api_key: Apollo.io API key
        
    Returns:
        Status dict with success/error info and connected status
    """
    state = _load_state()
    
    try:
        response = requests.get(
            f"{APOLLO_API_BASE}/auth/health",
            headers={
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
                "X-Api-Key": api_key
            },
            timeout=10
        )
        
        if response.status_code == 200:
            state.api_key = api_key
            state.connected = True
            state.last_error = None
            _save_state(state)
            print(f"[APOLLO] Connected successfully with API key (metadata-only mode)")
            return {"success": True, "connected": True, "message": "Apollo connected (metadata-only mode)"}
        elif response.status_code == 401 or response.status_code == 403:
            state.last_error = "Invalid API key"
            _save_state(state)
            return {"success": False, "connected": False, "error": "Invalid API key - check your key at apollo.io/settings/api-keys"}
        else:
            error_msg = f"API health check returned {response.status_code}"
            state.last_error = error_msg
            _save_state(state)
            return {"success": False, "connected": False, "error": error_msg}
                
    except requests.Timeout:
        state.last_error = "Connection timeout"
        _save_state(state)
        return {"success": False, "connected": False, "error": "Connection timeout - try again"}
    except Exception as e:
        state.last_error = str(e)
        _save_state(state)
        return {"success": False, "connected": False, "error": f"Connection error: {str(e)}"}


def auto_connect_from_env() -> Dict[str, Any]:
    """
    Auto-connect Apollo if APOLLO_API_KEY environment secret exists.
    
    Called on startup to provide frictionless Apollo integration.
    No user interaction required - just set the secret and it works.
    
    Returns:
        Status dict with connected status and message
    """
    apollo_key = os.getenv("APOLLO_API_KEY", "").strip()
    
    if not apollo_key:
        return {
            "success": False,
            "connected": False,
            "message": "APOLLO_API_KEY not set - lead generation paused"
        }
    
    state = _load_state()
    if state.connected and state.api_key == apollo_key:
        return {
            "success": True,
            "connected": True,
            "message": "Already connected via environment secret"
        }
    
    result = connect_apollo_with_key(apollo_key)
    
    if result.get("connected"):
        return {
            "success": True,
            "connected": True,
            "message": "Auto-connected from APOLLO_API_KEY secret"
        }
    else:
        return {
            "success": False,
            "connected": False,
            "message": f"Failed to connect with APOLLO_API_KEY: {result.get('error', 'Unknown error')}"
        }


def disconnect_apollo() -> Dict[str, Any]:
    """Disconnect Apollo integration."""
    state = ApolloState()
    _save_state(state)
    print("[APOLLO] Disconnected")
    return {"success": True, "message": "Apollo disconnected"}


def fetch_leads_from_apollo(
    location: str = "Miami",
    niche: str = "HVAC",
    min_size: int = 1,
    max_size: int = 200,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Fetch leads from Apollo.io - DISABLED (People Search requires paid tier).
    
    This function is now a stub that returns empty results.
    Lead generation now uses SignalNet instead of Apollo People Search.
    
    Args:
        location: Target city/region (default: Miami)
        niche: Industry/niche to target
        min_size: Minimum company size
        max_size: Maximum company size
        limit: Max leads to return
        
    Returns:
        Dict with empty leads array and disabled status
    """
    print("[APOLLO] People Search disabled - using SignalNet for leads")
    print(f"[APOLLO] Request for {location}/{niche} ignored (metadata-only mode)")
    
    _log_fetch(
        {"location": location, "niche": niche, "reason": "people_search_disabled"},
        0,
        True,
        "People Search API disabled - metadata only mode"
    )
    
    return {
        "success": True,
        "leads": [],
        "total_available": 0,
        "calls_remaining": DAILY_LIMIT,
        "people_search_disabled": True,
        "message": "People Search disabled - using SignalNet for leads"
    }


def get_fetch_log(limit: int = 50) -> List[Dict]:
    """Get recent Apollo fetch log entries."""
    entries = []
    try:
        if Path(APOLLO_FETCH_LOG).exists():
            with open(APOLLO_FETCH_LOG, 'r') as f:
                lines = f.readlines()
                for line in lines[-limit:]:
                    try:
                        entries.append(json.loads(line.strip()))
                    except:
                        pass
    except Exception as e:
        print(f"[APOLLO] Error reading log: {e}")
    return entries


def get_apollo_company_metadata(domain: str) -> Optional[dict]:
    """
    Get company metadata from Apollo (free tier friendly).
    Does NOT use People Search API.
    
    Uses the /v1/organizations/enrich endpoint which is available
    on free tier for company/organization data only.
    
    Args:
        domain: Company domain (e.g., "acme.com")
        
    Returns:
        Dict with company name, industry, size, or None if not found
    """
    state = _load_state()
    api_key = state.api_key or os.getenv("APOLLO_API_KEY", "")
    
    if not api_key and not state.access_token:
        print(f"[APOLLO] Cannot enrich {domain} - not connected")
        return None
    
    allowed, rate_msg = _check_rate_limit(state)
    if not allowed:
        print(f"[APOLLO] Cannot enrich {domain} - {rate_msg}")
        return None
    
    try:
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache"
        }
        
        if state.access_token:
            headers["Authorization"] = f"Bearer {state.access_token}"
        else:
            headers["X-Api-Key"] = api_key
        
        response = requests.get(
            f"{APOLLO_API_BASE}/organizations/enrich",
            headers=headers,
            params={"domain": domain},
            timeout=15
        )
        
        state.calls_today += 1
        state.last_fetch_at = datetime.utcnow().isoformat()
        
        if response.status_code != 200:
            print(f"[APOLLO] Organization enrich failed for {domain}: {response.status_code}")
            _save_state(state)
            return None
        
        data = response.json()
        org = data.get("organization", {})
        
        if not org:
            print(f"[APOLLO] No organization data for {domain}")
            _save_state(state)
            return None
        
        metadata = {
            "name": org.get("name"),
            "domain": org.get("primary_domain") or domain,
            "industry": org.get("industry"),
            "employee_count": org.get("estimated_num_employees"),
            "employee_range": org.get("employee_range"),
            "founded_year": org.get("founded_year"),
            "linkedin_url": org.get("linkedin_url"),
            "website_url": org.get("website_url"),
            "city": org.get("city"),
            "state": org.get("state"),
            "country": org.get("country"),
            "short_description": org.get("short_description"),
            "keywords": org.get("keywords", []),
            "apollo_id": org.get("id")
        }
        
        state.last_error = None
        _save_state(state)
        
        print(f"[APOLLO] Enriched {domain}: {metadata.get('name')} ({metadata.get('industry')})")
        
        return metadata
        
    except requests.Timeout:
        print(f"[APOLLO] Timeout enriching {domain}")
        state.last_error = "Request timeout"
        _save_state(state)
        return None
    except Exception as e:
        print(f"[APOLLO] Error enriching {domain}: {e}")
        state.last_error = str(e)
        _save_state(state)
        return None


def test_apollo_connection() -> Dict[str, Any]:
    """
    Test Apollo connection using health endpoint.
    
    NOTE: Metadata-only mode - does NOT test People Search.
    Tests connection validity without consuming paid API credits.
    """
    state = _load_state()
    api_key = state.api_key or os.getenv("APOLLO_API_KEY", "")
    
    if not api_key and not state.access_token:
        return {
            "success": False,
            "error": "Apollo not connected - add APOLLO_API_KEY to secrets",
            "mode": "metadata_only"
        }
    
    try:
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache"
        }
        
        if state.access_token:
            headers["Authorization"] = f"Bearer {state.access_token}"
        else:
            headers["X-Api-Key"] = api_key
        
        response = requests.get(
            f"{APOLLO_API_BASE}/auth/health",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            return {
                "success": True,
                "message": "Connection verified (metadata-only mode)",
                "mode": "metadata_only",
                "people_search_enabled": False,
                "note": "People Search disabled - using SignalNet for leads"
            }
        else:
            return {
                "success": False,
                "error": f"Health check failed: {response.status_code}",
                "mode": "metadata_only"
            }
            
    except requests.Timeout:
        return {
            "success": False,
            "error": "Connection timeout",
            "mode": "metadata_only"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "mode": "metadata_only"
        }
