"""
HossAgent Analytics Module

Server-side analytics for tracking:
- Page views and traffic sources
- Signup funnel (landing → signup → trial → upgrade)
- Abandonment events (signup started but not completed, checkout started but not completed)
- Customer engagement (portal visits, approvals, settings changes)

All data stored in PostgreSQL for admin dashboard visibility.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path


class EventType(str, Enum):
    PAGE_VIEW = "page_view"
    SIGNUP_STARTED = "signup_started"
    SIGNUP_COMPLETED = "signup_completed"
    SIGNUP_ABANDONED = "signup_abandoned"
    LOGIN = "login"
    PORTAL_VIEW = "portal_view"
    SETTINGS_VIEW = "settings_view"
    SETTINGS_UPDATED = "settings_updated"
    CHECKOUT_STARTED = "checkout_started"
    CHECKOUT_COMPLETED = "checkout_completed"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    UPGRADE_COMPLETED = "upgrade_completed"
    CANCELLATION = "cancellation"
    OUTREACH_APPROVED = "outreach_approved"
    OUTREACH_DISCARDED = "outreach_discarded"
    LEAD_VIEWED = "lead_viewed"
    REPORT_VIEWED = "report_viewed"


@dataclass
class AnalyticsEvent:
    """Single analytics event."""
    event_type: str
    timestamp: str
    path: Optional[str] = None
    referrer: Optional[str] = None
    user_agent: Optional[str] = None
    ip_hash: Optional[str] = None
    customer_id: Optional[int] = None
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


ANALYTICS_LOG_FILE = Path("analytics_events.json")
MAX_EVENTS = 5000


def _load_events() -> List[Dict[str, Any]]:
    """Load analytics events from file."""
    try:
        if ANALYTICS_LOG_FILE.exists():
            with open(ANALYTICS_LOG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_events(events: List[Dict[str, Any]]) -> None:
    """Save analytics events to file (capped)."""
    try:
        events = events[-MAX_EVENTS:]
        with open(ANALYTICS_LOG_FILE, "w") as f:
            json.dump(events, f, indent=2)
    except Exception as e:
        print(f"[ANALYTICS] Warning: Could not save events: {e}")


def _hash_ip(ip: str) -> str:
    """Hash IP for privacy (we don't store raw IPs)."""
    import hashlib
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def track_event(
    event_type: EventType,
    path: Optional[str] = None,
    referrer: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    customer_id: Optional[int] = None,
    session_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> None:
    """
    Track an analytics event.
    
    Args:
        event_type: Type of event (page_view, signup_started, etc.)
        path: URL path
        referrer: HTTP referrer
        user_agent: Browser user agent
        ip_address: Client IP (will be hashed)
        customer_id: Associated customer ID if logged in
        session_id: Session identifier
        metadata: Additional event-specific data
    """
    event = AnalyticsEvent(
        event_type=event_type.value if isinstance(event_type, EventType) else event_type,
        timestamp=datetime.utcnow().isoformat(),
        path=path,
        referrer=referrer,
        user_agent=user_agent[:200] if user_agent else None,
        ip_hash=_hash_ip(ip_address) if ip_address else None,
        customer_id=customer_id,
        session_id=session_id,
        metadata=metadata
    )
    
    events = _load_events()
    events.append(asdict(event))
    _save_events(events)
    
    print(f"[ANALYTICS] {event_type}: {path or 'N/A'} (customer: {customer_id or 'anon'})")


def track_page_view(
    path: str,
    referrer: Optional[str] = None,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    customer_id: Optional[int] = None,
    session_id: Optional[str] = None
) -> None:
    """Track a page view event."""
    track_event(
        EventType.PAGE_VIEW,
        path=path,
        referrer=referrer,
        user_agent=user_agent,
        ip_address=ip_address,
        customer_id=customer_id,
        session_id=session_id
    )


def track_funnel_event(
    event_type: EventType,
    customer_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None
) -> None:
    """Track a funnel event (signup, checkout, upgrade, etc.)."""
    track_event(
        event_type,
        customer_id=customer_id,
        metadata=metadata,
        ip_address=ip_address
    )


def get_events(
    limit: int = 100,
    event_type: Optional[str] = None,
    since: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Get analytics events with optional filtering."""
    events = _load_events()
    
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]
    
    if since:
        since_str = since.isoformat()
        events = [e for e in events if e.get("timestamp", "") >= since_str]
    
    return events[-limit:]


def get_page_view_stats(days: int = 7) -> Dict[str, Any]:
    """Get page view statistics for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    events = get_events(limit=10000, since=since)
    
    page_views = [e for e in events if e.get("event_type") == EventType.PAGE_VIEW.value]
    
    by_page: Dict[str, int] = {}
    by_day: Dict[str, int] = {}
    referrers: Dict[str, int] = {}
    
    for pv in page_views:
        path = pv.get("path", "unknown")
        by_page[path] = by_page.get(path, 0) + 1
        
        day = pv.get("timestamp", "")[:10]
        by_day[day] = by_day.get(day, 0) + 1
        
        ref = pv.get("referrer")
        if ref:
            ref_domain = ref.split("/")[2] if "//" in ref else ref
            referrers[ref_domain] = referrers.get(ref_domain, 0) + 1
    
    return {
        "total_views": len(page_views),
        "unique_visitors": len(set(e.get("ip_hash") for e in page_views if e.get("ip_hash"))),
        "by_page": dict(sorted(by_page.items(), key=lambda x: x[1], reverse=True)[:10]),
        "by_day": dict(sorted(by_day.items())),
        "top_referrers": dict(sorted(referrers.items(), key=lambda x: x[1], reverse=True)[:10]),
        "period_days": days
    }


def get_funnel_stats(days: int = 30) -> Dict[str, Any]:
    """Get conversion funnel statistics."""
    since = datetime.utcnow() - timedelta(days=days)
    events = get_events(limit=10000, since=since)
    
    landing_views = len([e for e in events if e.get("event_type") == EventType.PAGE_VIEW.value and e.get("path") == "/"])
    signup_started = len([e for e in events if e.get("event_type") == EventType.SIGNUP_STARTED.value])
    signup_completed = len([e for e in events if e.get("event_type") == EventType.SIGNUP_COMPLETED.value])
    signup_abandoned = len([e for e in events if e.get("event_type") == EventType.SIGNUP_ABANDONED.value])
    
    checkout_started = len([e for e in events if e.get("event_type") == EventType.CHECKOUT_STARTED.value])
    checkout_completed = len([e for e in events if e.get("event_type") == EventType.CHECKOUT_COMPLETED.value])
    checkout_abandoned = len([e for e in events if e.get("event_type") == EventType.CHECKOUT_ABANDONED.value])
    
    upgrades = len([e for e in events if e.get("event_type") == EventType.UPGRADE_COMPLETED.value])
    cancellations = len([e for e in events if e.get("event_type") == EventType.CANCELLATION.value])
    
    logins = len([e for e in events if e.get("event_type") == EventType.LOGIN.value])
    portal_views = len([e for e in events if e.get("event_type") == EventType.PORTAL_VIEW.value])
    
    signup_rate = (signup_completed / signup_started * 100) if signup_started > 0 else 0
    checkout_rate = (checkout_completed / checkout_started * 100) if checkout_started > 0 else 0
    upgrade_rate = (upgrades / signup_completed * 100) if signup_completed > 0 else 0
    
    return {
        "period_days": days,
        "funnel": {
            "landing_views": landing_views,
            "signup_started": signup_started,
            "signup_completed": signup_completed,
            "signup_abandoned": signup_abandoned,
            "signup_conversion_rate": round(signup_rate, 1),
            "checkout_started": checkout_started,
            "checkout_completed": checkout_completed,
            "checkout_abandoned": checkout_abandoned,
            "checkout_conversion_rate": round(checkout_rate, 1),
            "upgrades": upgrades,
            "upgrade_rate": round(upgrade_rate, 1),
            "cancellations": cancellations
        },
        "engagement": {
            "logins": logins,
            "portal_views": portal_views,
            "unique_active_customers": len(set(
                e.get("customer_id") for e in events 
                if e.get("customer_id") and e.get("event_type") in [
                    EventType.LOGIN.value, EventType.PORTAL_VIEW.value
                ]
            ))
        }
    }


def get_abandonment_details(days: int = 7) -> Dict[str, Any]:
    """Get abandonment event details for analysis."""
    since = datetime.utcnow() - timedelta(days=days)
    events = get_events(limit=10000, since=since)
    
    signup_abandons = [
        e for e in events 
        if e.get("event_type") == EventType.SIGNUP_ABANDONED.value
    ]
    
    checkout_abandons = [
        e for e in events 
        if e.get("event_type") == EventType.CHECKOUT_ABANDONED.value
    ]
    
    return {
        "period_days": days,
        "signup_abandons": {
            "count": len(signup_abandons),
            "recent": signup_abandons[-10:]
        },
        "checkout_abandons": {
            "count": len(checkout_abandons),
            "recent": checkout_abandons[-10:]
        }
    }


def get_analytics_summary() -> Dict[str, Any]:
    """Get overall analytics summary for admin dashboard."""
    return {
        "page_views_7d": get_page_view_stats(7),
        "funnel_30d": get_funnel_stats(30),
        "abandonment_7d": get_abandonment_details(7),
        "last_updated": datetime.utcnow().isoformat()
    }
