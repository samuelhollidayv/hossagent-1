"""
Lead Enrichment Pipeline - Free Tier Services Only

Enriches LeadEvents with contact information using:
1. Hunter.io - Domain to email (free tier: 25 requests/month)
2. Clearbit Logo/Company API - Company info (free tier available)
3. Website scraping - Contact/About/Team pages
4. Social link extraction - Facebook, Instagram, LinkedIn URLs

Pipeline runs asynchronously and respects rate limits.

============================================================================
ENVIRONMENT VARIABLES
============================================================================
HUNTER_API_KEY: Optional - Hunter.io API key for email discovery
CLEARBIT_API_KEY: Optional - Clearbit API key for company enrichment
ENRICHMENT_DRY_RUN: If true, skip actual API calls and log intentions

If no API keys are set, the pipeline falls back to web scraping only.
============================================================================
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from sqlmodel import Session, select

from models import (
    LeadEvent,
    Lead,
    ENRICHMENT_STATUS_UNENRICHED,
    ENRICHMENT_STATUS_ENRICHED,
    ENRICHMENT_STATUS_FAILED,
    ENRICHMENT_STATUS_SKIPPED,
)


HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
CLEARBIT_API_KEY = os.environ.get("CLEARBIT_API_KEY", "")
ENRICHMENT_DRY_RUN = os.environ.get("ENRICHMENT_DRY_RUN", "false").lower() in ("true", "1", "yes")

HUNTER_API_BASE = "https://api.hunter.io/v2"
CLEARBIT_API_BASE = "https://company.clearbit.com/v2"

REQUEST_TIMEOUT = 10
RATE_LIMIT_DELAY = 1.0
MAX_RETRIES = 2

CONTACT_PAGE_PATHS = [
    "/contact", "/contact-us", "/contact_us", "/contactus",
    "/about", "/about-us", "/about_us", "/aboutus",
    "/team", "/our-team", "/our_team", "/ourteam",
    "/connect", "/get-in-touch", "/reach-us", "/reach-out",
    "/support", "/help", "/inquiries", "/inquiry",
    "/locations", "/location", "/offices", "/office",
    "/staff", "/leadership", "/management", "/people",
    "/company", "/who-we-are", "/meet-the-team",
]

MAILTO_REGEX = re.compile(r'href=["\']mailto:([^"\'?]+)', re.IGNORECASE)

CONTACT_LINK_PATTERNS = [
    r'href=["\']([^"\']*contact[^"\']*)["\']',
    r'href=["\']([^"\']*about[^"\']*)["\']',
    r'href=["\']([^"\']*team[^"\']*)["\']',
    r'href=["\']([^"\']*get-in-touch[^"\']*)["\']',
    r'href=["\']([^"\']*reach[^"\']*)["\']',
]

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

PHONE_REGEX = re.compile(
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
    re.IGNORECASE
)

SOCIAL_PATTERNS = {
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[a-zA-Z0-9._-]+/?", re.IGNORECASE),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9._-]+/?", re.IGNORECASE),
    "linkedin": re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[a-zA-Z0-9._-]+/?", re.IGNORECASE),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[a-zA-Z0-9._-]+/?", re.IGNORECASE),
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def _get_dry_run_prefix() -> str:
    """Get log prefix for dry run mode."""
    return "[DRY_RUN]" if ENRICHMENT_DRY_RUN else ""


def log_enrichment(
    action: str,
    domain: Optional[str] = None,
    lead_event_id: Optional[int] = None,
    source: Optional[str] = None,
    details: Optional[Dict] = None,
    error: Optional[str] = None,
    success: bool = True
) -> None:
    """
    Structured logging for enrichment pipeline activity.
    
    Args:
        action: Action being performed (attempt, success, failure, skip, rate_limit)
        domain: Target domain being enriched
        lead_event_id: LeadEvent ID if applicable
        source: Enrichment source (hunter, clearbit, scrape)
        details: Additional context data
        error: Error message if any
        success: Whether the operation succeeded
    """
    prefix = _get_dry_run_prefix()
    timestamp = datetime.utcnow().isoformat()
    
    log_level = "ERROR" if error else ("INFO" if success else "WARN")
    domain_part = f" | Domain: {domain}" if domain else ""
    event_part = f" | LeadEvent: {lead_event_id}" if lead_event_id else ""
    source_part = f" | Source: {source}" if source else ""
    details_str = f" | {json.dumps(details)[:150]}" if details else ""
    error_part = f" | Error: {error}" if error else ""
    
    print(f"{prefix}[ENRICHMENT][{action.upper()}]{domain_part}{event_part}{source_part}{details_str}{error_part}")


@dataclass
class EnrichmentResult:
    """Result of an enrichment attempt."""
    success: bool
    source: str  # 'hunter', 'clearbit', 'scrape', 'none'
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_name: Optional[str] = None
    company_name: Optional[str] = None
    social_links: dict = field(default_factory=dict)
    error: Optional[str] = None


def extract_domain_from_url(url: str) -> Optional[str]:
    """
    Extract clean domain from URL.
    
    Args:
        url: Full URL or partial domain
        
    Returns:
        Clean domain without protocol/path, or None if invalid
    """
    if not url:
        return None
    
    url = url.strip().lower()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]
        domain = domain.replace("www.", "")
        if "." in domain and len(domain) > 3:
            return domain
    except Exception:
        pass
    
    return None


def try_hunter_enrichment(domain: str) -> Optional[dict]:
    """
    Use Hunter.io domain search API (free tier) to find emails.
    
    Free tier: 25 searches/month
    API: https://api.hunter.io/v2/domain-search
    
    Args:
        domain: Target domain to search
        
    Returns:
        Dict with emails and contact info, or None if failed/unavailable
    """
    if not HUNTER_API_KEY:
        log_enrichment("skip", domain=domain, source="hunter", 
                       details={"reason": "HUNTER_API_KEY not set"})
        return None
    
    if ENRICHMENT_DRY_RUN:
        log_enrichment("dry_run", domain=domain, source="hunter",
                       details={"would_call": f"{HUNTER_API_BASE}/domain-search"})
        return {
            "email": f"contact@{domain}",
            "contact_name": "Mock Contact",
            "source": "hunter_mock"
        }
    
    try:
        log_enrichment("attempt", domain=domain, source="hunter")
        
        response = requests.get(
            f"{HUNTER_API_BASE}/domain-search",
            params={
                "domain": domain,
                "api_key": HUNTER_API_KEY,
                "limit": 5
            },
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code == 429:
            log_enrichment("rate_limit", domain=domain, source="hunter",
                           error="Rate limit exceeded")
            return None
        
        if response.status_code == 402:
            log_enrichment("quota_exceeded", domain=domain, source="hunter",
                           error="Monthly quota exceeded (free tier: 25/month)")
            return None
        
        if response.status_code != 200:
            log_enrichment("failure", domain=domain, source="hunter",
                           error=f"HTTP {response.status_code}")
            return None
        
        data = response.json()
        
        if not data.get("data"):
            log_enrichment("no_results", domain=domain, source="hunter")
            return None
        
        result_data = data["data"]
        emails = result_data.get("emails", [])
        
        if not emails:
            log_enrichment("no_emails", domain=domain, source="hunter")
            return None
        
        best_email = emails[0]
        
        result = {
            "email": best_email.get("value"),
            "contact_name": None,
            "company_name": result_data.get("organization"),
            "source": "hunter"
        }
        
        first_name = best_email.get("first_name")
        last_name = best_email.get("last_name")
        if first_name or last_name:
            result["contact_name"] = f"{first_name or ''} {last_name or ''}".strip()
        
        social_links = {}
        if result_data.get("facebook"):
            social_links["facebook"] = result_data["facebook"]
        if result_data.get("twitter"):
            social_links["twitter"] = result_data["twitter"]
        if result_data.get("linkedin"):
            social_links["linkedin"] = result_data["linkedin"]
        if result_data.get("instagram"):
            social_links["instagram"] = result_data["instagram"]
        
        if social_links:
            result["social_links"] = social_links
        
        log_enrichment("success", domain=domain, source="hunter",
                       details={"email_found": result["email"]})
        
        return result
        
    except requests.Timeout:
        log_enrichment("timeout", domain=domain, source="hunter",
                       error="Request timed out")
        return None
    except requests.RequestException as e:
        log_enrichment("error", domain=domain, source="hunter",
                       error=str(e))
        return None
    except Exception as e:
        log_enrichment("error", domain=domain, source="hunter",
                       error=f"Unexpected: {str(e)}")
        return None


def try_clearbit_enrichment(domain: str) -> Optional[dict]:
    """
    Use Clearbit company lookup for company info and social links.
    
    Free tier available with limited requests.
    API: https://company.clearbit.com/v2/companies/find
    
    Args:
        domain: Target domain to lookup
        
    Returns:
        Dict with company info and social links, or None if failed/unavailable
    """
    if not CLEARBIT_API_KEY:
        log_enrichment("skip", domain=domain, source="clearbit",
                       details={"reason": "CLEARBIT_API_KEY not set"})
        return None
    
    if ENRICHMENT_DRY_RUN:
        log_enrichment("dry_run", domain=domain, source="clearbit",
                       details={"would_call": f"{CLEARBIT_API_BASE}/companies/find"})
        return {
            "company_name": f"Mock Company ({domain})",
            "description": "Mock company description",
            "social_links": {
                "linkedin": f"https://linkedin.com/company/{domain.split('.')[0]}",
                "twitter": f"https://twitter.com/{domain.split('.')[0]}"
            },
            "source": "clearbit_mock"
        }
    
    try:
        log_enrichment("attempt", domain=domain, source="clearbit")
        
        response = requests.get(
            f"{CLEARBIT_API_BASE}/companies/find",
            params={"domain": domain},
            headers={"Authorization": f"Bearer {CLEARBIT_API_KEY}"},
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code == 429:
            log_enrichment("rate_limit", domain=domain, source="clearbit",
                           error="Rate limit exceeded")
            return None
        
        if response.status_code == 402:
            log_enrichment("quota_exceeded", domain=domain, source="clearbit",
                           error="Quota exceeded")
            return None
        
        if response.status_code == 404:
            log_enrichment("not_found", domain=domain, source="clearbit")
            return None
        
        if response.status_code != 200:
            log_enrichment("failure", domain=domain, source="clearbit",
                           error=f"HTTP {response.status_code}")
            return None
        
        data = response.json()
        
        if not data:
            log_enrichment("no_data", domain=domain, source="clearbit")
            return None
        
        result = {
            "company_name": data.get("name"),
            "description": data.get("description"),
            "source": "clearbit"
        }
        
        social_links = {}
        if data.get("facebook", {}).get("handle"):
            social_links["facebook"] = f"https://facebook.com/{data['facebook']['handle']}"
        if data.get("twitter", {}).get("handle"):
            social_links["twitter"] = f"https://twitter.com/{data['twitter']['handle']}"
        if data.get("linkedin", {}).get("handle"):
            social_links["linkedin"] = f"https://linkedin.com/company/{data['linkedin']['handle']}"
        
        if social_links:
            result["social_links"] = social_links
        
        log_enrichment("success", domain=domain, source="clearbit",
                       details={"company": result.get("company_name")})
        
        return result
        
    except requests.Timeout:
        log_enrichment("timeout", domain=domain, source="clearbit",
                       error="Request timed out")
        return None
    except requests.RequestException as e:
        log_enrichment("error", domain=domain, source="clearbit",
                       error=str(e))
        return None
    except Exception as e:
        log_enrichment("error", domain=domain, source="clearbit",
                       error=f"Unexpected: {str(e)}")
        return None


def extract_social_links(html: str) -> dict:
    """
    Extract social media profile URLs from HTML content.
    
    Finds Facebook, Instagram, LinkedIn, and Twitter/X URLs.
    
    Args:
        html: Raw HTML content to search
        
    Returns:
        Dict of social platform -> URL mappings
    """
    if not html:
        return {}
    
    social_links = {}
    
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = pattern.findall(html)
        if matches:
            url = matches[0].rstrip("/")
            if not any(skip in url.lower() for skip in ["share", "sharer", "intent", "dialog"]):
                social_links[platform] = url
    
    return social_links


def _extract_emails_from_html(html: str, domain: str) -> List[str]:
    """
    Extract email addresses from HTML, prioritizing domain matches.
    
    Args:
        html: Raw HTML content
        domain: Target domain for prioritization
        
    Returns:
        List of unique email addresses
    """
    if not html:
        return []
    
    emails = EMAIL_REGEX.findall(html)
    
    emails = list(set(emails))
    
    skip_patterns = ["example.com", "domain.com", "email.com", "yoursite.com", 
                     "placeholder", "test@", "noreply", "no-reply", ".png", ".jpg", ".gif"]
    emails = [e for e in emails if not any(skip in e.lower() for skip in skip_patterns)]
    
    domain_emails = [e for e in emails if domain in e.lower()]
    other_emails = [e for e in emails if domain not in e.lower()]
    
    return domain_emails + other_emails


def _extract_phones_from_html(html: str) -> List[str]:
    """
    Extract phone numbers from HTML content.
    
    Args:
        html: Raw HTML content
        
    Returns:
        List of unique phone numbers
    """
    if not html:
        return []
    
    phones = PHONE_REGEX.findall(html)
    
    phones = list(set(phones))
    
    phones = [p for p in phones if len(re.sub(r'\D', '', p)) >= 10]
    
    return phones[:5]


def scrape_contact_page(domain: str) -> Optional[dict]:
    """
    Scrape website contact/about/team pages for contact info.
    
    Fetches common contact pages and extracts:
    - Email addresses (regex)
    - Phone numbers (regex)
    - Social media links
    
    Uses browser-like headers to avoid blocks.
    
    Args:
        domain: Target domain to scrape
        
    Returns:
        Dict with extracted contact info, or None if nothing found
    """
    if ENRICHMENT_DRY_RUN:
        log_enrichment("dry_run", domain=domain, source="scrape",
                       details={"would_fetch": CONTACT_PAGE_PATHS})
        return {
            "email": f"info@{domain}",
            "phone": "(555) 123-4567",
            "social_links": {
                "facebook": f"https://facebook.com/{domain.split('.')[0]}",
            },
            "source": "scrape_mock"
        }
    
    log_enrichment("attempt", domain=domain, source="scrape")
    
    base_url = f"https://{domain}"
    all_emails = []
    all_phones = []
    all_social = {}
    pages_tried = 0
    pages_success = 0
    
    for path in CONTACT_PAGE_PATHS:
        url = urljoin(base_url, path)
        pages_tried += 1
        
        try:
            response = requests.get(
                url,
                headers=BROWSER_HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )
            
            if response.status_code == 200:
                pages_success += 1
                html = response.text
                
                emails = _extract_emails_from_html(html, domain)
                all_emails.extend(emails)
                
                phones = _extract_phones_from_html(html)
                all_phones.extend(phones)
                
                social = extract_social_links(html)
                all_social.update(social)
            
            time.sleep(0.5)
            
        except requests.Timeout:
            log_enrichment("page_timeout", domain=domain, source="scrape",
                           details={"url": url})
            continue
        except requests.RequestException as e:
            continue
        except Exception as e:
            log_enrichment("page_error", domain=domain, source="scrape",
                           details={"url": url}, error=str(e))
            continue
    
    try:
        response = requests.get(
            base_url,
            headers=BROWSER_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
        
        if response.status_code == 200:
            html = response.text
            
            if not all_emails:
                emails = _extract_emails_from_html(html, domain)
                all_emails.extend(emails)
            
            if not all_social:
                social = extract_social_links(html)
                all_social.update(social)
                
    except Exception:
        pass
    
    all_emails = list(dict.fromkeys(all_emails))[:5]
    all_phones = list(dict.fromkeys(all_phones))[:3]
    
    if not all_emails and not all_phones and not all_social:
        log_enrichment("no_data", domain=domain, source="scrape",
                       details={"pages_tried": pages_tried, "pages_success": pages_success})
        return None
    
    result: Dict[str, Any] = {
        "source": "scrape"
    }
    
    if all_emails:
        result["email"] = all_emails[0]
        if len(all_emails) > 1:
            result["additional_emails"] = all_emails[1:]
    
    if all_phones:
        result["phone"] = all_phones[0]
        if len(all_phones) > 1:
            result["additional_phones"] = all_phones[1:]
    
    if all_social:
        result["social_links"] = all_social
    
    log_enrichment("success", domain=domain, source="scrape",
                   details={
                       "emails_found": len(all_emails),
                       "phones_found": len(all_phones),
                       "social_found": len(all_social),
                       "pages_success": pages_success
                   })
    
    return result


async def enrich_lead_event(lead_event: LeadEvent, session: Session) -> EnrichmentResult:
    """
    Main entry point for enriching a LeadEvent with contact information.
    
    Tries enrichment sources in order of preference:
    1. Hunter.io (if API key set)
    2. Clearbit (if API key set)
    3. Website scraping (always available)
    
    Merges results from multiple sources when beneficial.
    
    Args:
        lead_event: LeadEvent to enrich
        session: Database session for Lead lookup
        
    Returns:
        EnrichmentResult with status and data
    """
    log_enrichment("start", lead_event_id=lead_event.id)
    
    domain = None
    
    if lead_event.lead_id:
        lead = session.exec(
            select(Lead).where(Lead.id == lead_event.lead_id)
        ).first()
        
        if lead and lead.website:
            domain = extract_domain_from_url(lead.website)
    
    if not domain:
        summary_lower = lead_event.summary.lower() if lead_event.summary else ""
        url_match = re.search(r"https?://[^\s]+", summary_lower)
        if url_match:
            domain = extract_domain_from_url(url_match.group())
    
    if not domain:
        log_enrichment("skip", lead_event_id=lead_event.id,
                       details={"reason": "No domain available"})
        return EnrichmentResult(
            success=False,
            source="none",
            error="No domain available for enrichment"
        )
    
    result = EnrichmentResult(success=False, source="none")
    
    time.sleep(RATE_LIMIT_DELAY)
    
    hunter_data = try_hunter_enrichment(domain)
    
    if hunter_data and hunter_data.get("email"):
        result.success = True
        result.source = "hunter"
        result.email = hunter_data.get("email")
        result.contact_name = hunter_data.get("contact_name")
        result.company_name = hunter_data.get("company_name")
        if hunter_data.get("social_links"):
            result.social_links = hunter_data["social_links"]
    
    if not result.company_name or not result.social_links:
        time.sleep(RATE_LIMIT_DELAY)
        clearbit_data = try_clearbit_enrichment(domain)
        
        if clearbit_data:
            if not result.company_name:
                result.company_name = clearbit_data.get("company_name")
            
            if clearbit_data.get("social_links"):
                if not result.social_links:
                    result.social_links = clearbit_data["social_links"]
                else:
                    for platform, url in clearbit_data["social_links"].items():
                        if platform not in result.social_links:
                            result.social_links[platform] = url
            
            if not result.success and (clearbit_data.get("company_name") or clearbit_data.get("social_links")):
                result.success = True
                result.source = "clearbit"
    
    if not result.email or not result.phone or not result.social_links:
        time.sleep(RATE_LIMIT_DELAY)
        scrape_data = scrape_contact_page(domain)
        
        if scrape_data:
            if not result.email:
                result.email = scrape_data.get("email")
            
            if not result.phone:
                result.phone = scrape_data.get("phone")
            
            if scrape_data.get("social_links"):
                if not result.social_links:
                    result.social_links = scrape_data["social_links"]
                else:
                    for platform, url in scrape_data["social_links"].items():
                        if platform not in result.social_links:
                            result.social_links[platform] = url
            
            if not result.success and (scrape_data.get("email") or scrape_data.get("phone") or scrape_data.get("social_links")):
                result.success = True
                result.source = "scrape"
    
    if result.success:
        log_enrichment("complete", domain=domain, lead_event_id=lead_event.id,
                       source=result.source,
                       details={
                           "has_email": bool(result.email),
                           "has_phone": bool(result.phone),
                           "has_contact": bool(result.contact_name),
                           "social_count": len(result.social_links)
                       })
    else:
        result.error = "No contact information found from any source"
        log_enrichment("failed", domain=domain, lead_event_id=lead_event.id,
                       error=result.error)
    
    return result


def _apply_enrichment_to_lead_event(
    lead_event: LeadEvent,
    result: EnrichmentResult,
    session: Session
) -> None:
    """
    Apply enrichment results to LeadEvent and persist to database.
    
    Args:
        lead_event: LeadEvent to update
        result: EnrichmentResult with data
        session: Database session
    """
    if result.success:
        lead_event.enrichment_status = ENRICHMENT_STATUS_ENRICHED
        lead_event.enrichment_source = result.source
        lead_event.enriched_email = result.email
        lead_event.enriched_phone = result.phone
        lead_event.enriched_contact_name = result.contact_name
        lead_event.enriched_company_name = result.company_name
        lead_event.enriched_social_links = json.dumps(result.social_links) if result.social_links else None
        lead_event.enriched_at = datetime.utcnow()
    else:
        lead_event.enrichment_status = ENRICHMENT_STATUS_FAILED
        lead_event.enrichment_source = "none"
    
    session.add(lead_event)
    session.commit()


MAX_ENRICHMENT_PER_CYCLE = int(os.environ.get("MAX_ENRICHMENT_PER_CYCLE", "5"))


async def run_enrichment_pipeline(session: Session, max_events: int = None) -> dict:
    """
    Process UNENRICHED LeadEvents through the enrichment pipeline.
    
    Fetches LeadEvents with enrichment_status='UNENRICHED', attempts
    enrichment for each, and updates their status.
    
    Processes a limited batch per cycle to avoid blocking (default: 15).
    Respects rate limits by adding delays between requests.
    
    Args:
        session: Database session
        max_events: Maximum events to process per cycle (default from MAX_ENRICHMENT_PER_CYCLE env var)
        
    Returns:
        Summary dict with stats:
        - processed: Number of events processed
        - enriched: Number successfully enriched
        - failed: Number that failed enrichment
        - skipped: Number skipped (no domain)
        - pending: Number of events still awaiting enrichment
        - by_source: Breakdown by enrichment source
    """
    if max_events is None:
        max_events = MAX_ENRICHMENT_PER_CYCLE
        
    log_enrichment("pipeline_start", details={"status": "starting", "max_events": max_events})
    
    unenriched_events = session.exec(
        select(LeadEvent)
        .where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_UNENRICHED)
        .limit(max_events)
    ).all()
    
    total_unenriched = len(session.exec(
        select(LeadEvent).where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_UNENRICHED)
    ).all())
    
    total = len(unenriched_events)
    pending_after = total_unenriched - total
    log_enrichment("pipeline_load", details={"batch_size": total, "total_unenriched": total_unenriched})
    
    if total == 0:
        log_enrichment("pipeline_complete", details={"message": "No unenriched events"})
        return {
            "processed": 0,
            "enriched": 0,
            "failed": 0,
            "skipped": 0,
            "pending": 0,
            "by_source": {}
        }
    
    stats = {
        "processed": 0,
        "enriched": 0,
        "failed": 0,
        "skipped": 0,
        "pending": pending_after,
        "by_source": {
            "hunter": 0,
            "clearbit": 0,
            "scrape": 0,
            "none": 0
        }
    }
    
    for i, lead_event in enumerate(unenriched_events):
        result = await enrich_lead_event(lead_event, session)
        
        stats["processed"] += 1
        
        if result.source == "none" and result.error and "No domain" in result.error:
            lead_event.enrichment_status = ENRICHMENT_STATUS_SKIPPED
            session.add(lead_event)
            session.commit()
            stats["skipped"] += 1
            stats["by_source"]["none"] += 1
        elif result.success:
            _apply_enrichment_to_lead_event(lead_event, result, session)
            stats["enriched"] += 1
            stats["by_source"][result.source] += 1
        else:
            _apply_enrichment_to_lead_event(lead_event, result, session)
            stats["failed"] += 1
            stats["by_source"]["none"] += 1
        
        if (i + 1) % 10 == 0:
            log_enrichment("pipeline_progress", details={
                "processed": i + 1,
                "total": total,
                "enriched": stats["enriched"],
                "failed": stats["failed"]
            })
        
        if i < total - 1:
            await asyncio.sleep(RATE_LIMIT_DELAY)
    
    log_enrichment("pipeline_complete", details=stats)
    
    return stats


def get_enrichment_status() -> dict:
    """
    Get current enrichment pipeline status and API availability.
    
    Returns:
        Dict with status info:
        - hunter_available: Whether Hunter API key is set
        - clearbit_available: Whether Clearbit API key is set
        - scrape_only_mode: True if no API keys set
        - dry_run: Whether dry run mode is enabled
    """
    return {
        "hunter_available": bool(HUNTER_API_KEY),
        "clearbit_available": bool(CLEARBIT_API_KEY),
        "scrape_only_mode": not HUNTER_API_KEY and not CLEARBIT_API_KEY,
        "dry_run": ENRICHMENT_DRY_RUN
    }


print(f"{_get_dry_run_prefix()}[ENRICHMENT][STARTUP] Hunter: {'enabled' if HUNTER_API_KEY else 'disabled'}, Clearbit: {'enabled' if CLEARBIT_API_KEY else 'disabled'}, DRY_RUN: {ENRICHMENT_DRY_RUN}")
