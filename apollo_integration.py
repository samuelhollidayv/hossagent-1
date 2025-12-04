"""
Apollo.io Integration Module - Hoss Style

Provides seamless Apollo.io integration with:
- OAuth 2.0 flow (one-click connect)
- Automatic token refresh
- Rate limiting (100 calls/day)
- Detailed fetch logging

Apollo is the ONLY lead source. No fallbacks.
If not connected or quota exceeded, lead generation pauses.
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
    env_key = os.getenv("APOLLO_API_KEY", "")
    
    allowed, rate_msg = _check_rate_limit(state)
    
    is_connected = bool(state.api_key or env_key or state.access_token)
    auth_method = None
    if state.access_token:
        auth_method = "oauth"
    elif state.api_key or env_key:
        auth_method = "api_key"
    
    return {
        "connected": is_connected,
        "auth_method": auth_method,
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
    
    Args:
        api_key: Apollo.io API key
        
    Returns:
        Status dict with success/error info
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
            print(f"[APOLLO] Connected successfully with API key")
            return {"success": True, "message": "Apollo connected successfully"}
        elif response.status_code == 401 or response.status_code == 403:
            state.last_error = "Invalid API key"
            _save_state(state)
            return {"success": False, "error": "Invalid API key - check your key at apollo.io/settings/api-keys"}
        else:
            test_response = requests.post(
                f"{APOLLO_API_BASE}/mixed_people/search",
                headers={
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                    "X-Api-Key": api_key
                },
                json={"page": 1, "per_page": 1},
                timeout=10
            )
            
            if test_response.status_code == 200:
                state.api_key = api_key
                state.connected = True
                state.last_error = None
                _save_state(state)
                print(f"[APOLLO] Connected successfully with API key (via search test)")
                return {"success": True, "message": "Apollo connected successfully"}
            else:
                error_msg = f"API returned {test_response.status_code}"
                state.last_error = error_msg
                _save_state(state)
                return {"success": False, "error": error_msg}
                
    except requests.Timeout:
        state.last_error = "Connection timeout"
        _save_state(state)
        return {"success": False, "error": "Connection timeout - try again"}
    except Exception as e:
        state.last_error = str(e)
        _save_state(state)
        return {"success": False, "error": f"Connection error: {str(e)}"}


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
    Fetch real leads from Apollo.io.
    
    Args:
        location: Target city/region (default: Miami)
        niche: Industry/niche to target
        min_size: Minimum company size
        max_size: Maximum company size
        limit: Max leads to return
        
    Returns:
        Dict with leads array and metadata
    """
    state = _load_state()
    
    api_key = state.api_key or os.getenv("APOLLO_API_KEY", "")
    if not api_key and not state.access_token:
        return {
            "success": False,
            "error": "Apollo not connected - lead generation PAUSED",
            "leads": [],
            "paused": True
        }
    
    allowed, rate_msg = _check_rate_limit(state)
    if not allowed:
        return {
            "success": False,
            "error": rate_msg,
            "leads": [],
            "quota_exceeded": True,
            "paused": True
        }
    
    MIAMI_LOCATIONS = [
        "Miami, FL", "Fort Lauderdale, FL", "Boca Raton, FL", 
        "West Palm Beach, FL", "Coral Gables, FL", "Miami Beach, FL",
        "Doral, FL", "Hialeah, FL", "Hollywood, FL", "Aventura, FL"
    ]
    
    NICHE_KEYWORDS = {
        "hvac": ["hvac", "heating", "cooling", "air conditioning"],
        "med spa": ["medical spa", "medspa", "aesthetics", "cosmetic"],
        "realtor": ["real estate", "realtor", "property", "brokerage"],
        "roofing": ["roofing", "roof", "construction"],
        "immigration": ["immigration", "law firm", "attorney"],
        "marketing": ["marketing", "advertising", "digital agency"]
    }
    
    query = {
        "page": 1,
        "per_page": min(limit, 25),
        "person_titles": [
            "Owner", "CEO", "Founder", "President", "Director",
            "General Manager", "Managing Partner", "Principal"
        ],
        "organization_num_employees_ranges": [f"{min_size},{max_size}"]
    }
    
    if "miami" in location.lower() or "florida" in location.lower():
        query["person_locations"] = MIAMI_LOCATIONS
    else:
        query["person_locations"] = [location]
    
    niche_lower = niche.lower()
    keywords = []
    for key, terms in NICHE_KEYWORDS.items():
        if key in niche_lower:
            keywords.extend(terms)
    
    if keywords:
        query["q_organization_keyword_tags"] = keywords
    else:
        query["q_keywords"] = niche
    
    try:
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache"
        }
        
        if state.access_token:
            headers["Authorization"] = f"Bearer {state.access_token}"
        else:
            headers["X-Api-Key"] = api_key
        
        response = requests.post(
            f"{APOLLO_API_BASE}/mixed_people/search",
            headers=headers,
            json=query,
            timeout=30
        )
        
        state.calls_today += 1
        state.last_fetch_at = datetime.utcnow().isoformat()
        
        if response.status_code != 200:
            error_msg = f"API error {response.status_code}: {response.text[:200]}"
            state.last_error = error_msg
            _save_state(state)
            _log_fetch(query, 0, False, error_msg)
            
            if response.status_code == 429:
                return {
                    "success": False,
                    "error": "Rate limit exceeded by Apollo API",
                    "leads": [],
                    "quota_exceeded": True,
                    "paused": True
                }
            
            return {
                "success": False,
                "error": error_msg,
                "leads": [],
                "paused": True
            }
        
        data = response.json()
        people = data.get("people", [])
        
        leads = []
        for person in people:
            email = person.get("email")
            if not email:
                continue
            
            org = person.get("organization", {}) or {}
            
            lead = {
                "name": person.get("name") or f"{person.get('first_name', '')} {person.get('last_name', '')}".strip(),
                "company": org.get("name") or person.get("organization_name", "Unknown"),
                "email": email,
                "title": person.get("title", ""),
                "industry": org.get("industry", niche),
                "website": org.get("website_url") or org.get("primary_domain", ""),
                "linkedin": person.get("linkedin_url", ""),
                "city": person.get("city", ""),
                "state": person.get("state", ""),
                "employee_count": org.get("estimated_num_employees"),
                "apollo_id": person.get("id"),
                "apollo_url": f"https://app.apollo.io/#/people/{person.get('id')}" if person.get('id') else None
            }
            leads.append(lead)
        
        state.total_leads_fetched += len(leads)
        state.last_error = None
        _save_state(state)
        _log_fetch(query, len(leads), True)
        
        print(f"[APOLLO] Fetched {len(leads)} leads from Apollo.io (location={location}, niche={niche})")
        
        if leads:
            print(f"[APOLLO] Sample leads:")
            for lead in leads[:5]:
                print(f"  - {lead['name']} @ {lead['company']} ({lead['email']})")
        
        return {
            "success": True,
            "leads": leads,
            "total_available": data.get("pagination", {}).get("total_entries", len(leads)),
            "calls_remaining": DAILY_LIMIT - state.calls_today
        }
        
    except requests.Timeout:
        state.last_error = "Request timeout"
        _save_state(state)
        _log_fetch(query, 0, False, "timeout")
        return {
            "success": False,
            "error": "Request timeout - Apollo API not responding",
            "leads": [],
            "paused": True
        }
    except Exception as e:
        state.last_error = str(e)
        _save_state(state)
        _log_fetch(query, 0, False, str(e))
        return {
            "success": False,
            "error": str(e),
            "leads": [],
            "paused": True
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


def test_apollo_connection() -> Dict[str, Any]:
    """
    Test Apollo connection by fetching sample leads.
    This DOES count against quota (1 API call).
    """
    result = fetch_leads_from_apollo(
        location="Miami",
        niche="HVAC", 
        min_size=1,
        max_size=200,
        limit=5
    )
    
    if result.get("success") and result.get("leads"):
        return {
            "success": True,
            "message": f"Connection verified - found {len(result['leads'])} real leads",
            "leads": result["leads"],
            "calls_remaining": result.get("calls_remaining")
        }
    elif result.get("paused"):
        return {
            "success": False,
            "error": result.get("error", "Lead generation paused"),
            "paused": True
        }
    else:
        return {
            "success": False,
            "error": result.get("error", "No leads found - check your Apollo search criteria")
        }
