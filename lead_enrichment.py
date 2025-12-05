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
    Signal,
    ENRICHMENT_STATUS_UNENRICHED,
    ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL,
    ENRICHMENT_STATUS_WITH_PHONE_ONLY,
    ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND,
    ENRICHMENT_STATUS_OUTBOUND_SENT,
    ENRICHMENT_STATUS_ARCHIVED,
    ENRICHMENT_STATUS_ENRICHED,
    ENRICHMENT_STATUS_FAILED,
    ENRICHMENT_STATUS_SKIPPED,
)
from domain_discovery import discover_domain_for_lead_event, DomainDiscoveryResult, extract_company_name_from_summary
from outbound_utils import send_lead_event_immediate
from phone_extraction import discover_phones, get_domain_from_phone, PhoneDiscoveryResult


HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
CLEARBIT_API_KEY = os.environ.get("CLEARBIT_API_KEY", "")
ENRICHMENT_DRY_RUN = os.environ.get("ENRICHMENT_DRY_RUN", "false").lower() in ("true", "1", "yes")

HUNTER_API_BASE = "https://api.hunter.io/v2"
CLEARBIT_API_BASE = "https://company.clearbit.com/v2"

REQUEST_TIMEOUT = 10
RATE_LIMIT_DELAY = 1.0
MAX_RETRIES = 2

CONTACT_PAGE_PATHS = [
    "/contact", "/contact-us", "/contact_us", "/contactus", "/contact-page",
    "/about", "/about-us", "/about_us", "/aboutus", "/about-page",
    "/team", "/our-team", "/our_team", "/ourteam", "/team-page", "/meet-team",
    "/connect", "/get-in-touch", "/reach-us", "/reach-out", "/lets-talk",
    "/support", "/help", "/inquiries", "/inquiry", "/help-center",
    "/locations", "/location", "/offices", "/office", "/service-areas",
    "/staff", "/leadership", "/management", "/people", "/careers",
    "/company", "/who-we-are", "/meet-the-team", "/about-company",
    "/business", "/services", "/contact-information", "/footer",
    "/agents", "/partners", "/testimonials", "/newsletter",
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

EMAIL_PATTERNS = [
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    re.compile(r'(?:email|mail|contact|reach|hello):\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', re.IGNORECASE),
    re.compile(r'(?:info|contact|hello|support|sales)@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', re.IGNORECASE),
]

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
    """Result of an enrichment attempt - ARCHANGEL Enhanced."""
    success: bool
    source: str  # 'hunter', 'clearbit', 'scrape', 'none'
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_name: Optional[str] = None
    company_name: Optional[str] = None
    social_links: dict = field(default_factory=dict)
    error: Optional[str] = None
    email_confidence: float = 0.0  # ARCHANGEL: email confidence score 0-1.0


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
    Extract email addresses from HTML using multiple patterns, prioritizing domain matches.
    
    Args:
        html: Raw HTML content
        domain: Target domain for prioritization
        
    Returns:
        List of unique email addresses
    """
    if not html:
        return []
    
    emails = set()
    
    for pattern in EMAIL_PATTERNS:
        matches = pattern.findall(html)
        for match in matches:
            if isinstance(match, tuple):
                match = match[1] if len(match) > 1 else match[0]
            emails.add(match)
    
    emails = list(emails)
    
    skip_patterns = ["example.com", "domain.com", "email.com", "yoursite.com", 
                     "placeholder", "test@", "noreply", "no-reply", ".png", ".jpg", ".gif",
                     "facebook.com", "instagram.com", "twitter.com", "linkedin.com"]
    emails = [e for e in emails if not any(skip in e.lower() for skip in skip_patterns) and "@" in e]
    
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


def _fetch_page(url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[str]:
    """Fetch a page with browser headers, return HTML or None."""
    try:
        response = requests.get(
            url,
            headers=BROWSER_HEADERS,
            timeout=timeout,
            allow_redirects=True
        )
        if response.status_code == 200:
            return response.text
    except Exception:
        pass
    return None


def _extract_mailto_emails(html: str) -> List[str]:
    """Extract emails from mailto: links - these are high confidence."""
    if not html:
        return []
    matches = MAILTO_REGEX.findall(html)
    return list(set(matches))


def _guess_domain_emails(domain: str) -> List[str]:
    """
    Generate common domain-based email guesses.
    
    Common patterns: info@, contact@, hello@, support@, sales@, admin@, etc.
    
    Args:
        domain: Target domain
        
    Returns:
        List of guessed email addresses
    """
    domain_clean = domain.replace("www.", "")
    prefixes = [
        "info", "contact", "hello", "support", "sales", "admin",
        "office", "team", "inquiry", "inquiries", "business", "hr",
        "marketing", "partnerships", "press", "feedback", "hello"
    ]
    
    guessed = []
    for prefix in prefixes:
        guessed.append(f"{prefix}@{domain_clean}")
    
    return guessed


def _discover_contact_links(html: str, base_url: str) -> List[str]:
    """Find links on a page that look like they lead to contact info."""
    discovered = set()
    for pattern in CONTACT_LINK_PATTERNS:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches:
            if match.startswith("http"):
                discovered.add(match)
            elif match.startswith("/"):
                discovered.add(urljoin(base_url, match))
            elif not match.startswith("#") and not match.startswith("javascript"):
                discovered.add(urljoin(base_url, "/" + match))
    return list(discovered)[:10]


def scrape_contact_page(domain: str) -> Optional[dict]:
    """
    AGGRESSIVE web scraper to find contact info from company websites.
    
    Strategy (tenacious multi-phase approach):
    1. Fetch homepage - extract mailto: links (highest confidence)
    2. Try common contact page paths (expanded list)
    3. Discover contact links from homepage and follow them
    4. Extract from footer sections
    5. Try both www and non-www versions
    6. Parse any emails found in page body
    
    Args:
        domain: Target domain to scrape
        
    Returns:
        Dict with extracted contact info, or None if nothing found
    """
    if ENRICHMENT_DRY_RUN:
        log_enrichment("dry_run", domain=domain, source="scrape",
                       details={"strategy": "aggressive_multi_phase"})
        return {
            "email": f"info@{domain}",
            "phone": "(555) 123-4567",
            "social_links": {"facebook": f"https://facebook.com/{domain.split('.')[0]}"},
            "source": "scrape_mock"
        }
    
    log_enrichment("attempt", domain=domain, source="scrape",
                   details={"strategy": "aggressive"})
    
    all_emails = []
    all_phones = []
    all_social = {}
    pages_tried = 0
    pages_success = 0
    discovered_links = []
    
    base_urls = [f"https://{domain}", f"https://www.{domain}"]
    if domain.startswith("www."):
        base_urls = [f"https://{domain}", f"https://{domain[4:]}"]
    
    homepage_html = None
    working_base = None
    
    for base_url in base_urls:
        html = _fetch_page(base_url)
        pages_tried += 1
        if html:
            homepage_html = html
            working_base = base_url
            pages_success += 1
            
            mailto_emails = _extract_mailto_emails(html)
            if mailto_emails:
                all_emails.extend(mailto_emails)
                log_enrichment("mailto_found", domain=domain, source="scrape",
                               details={"count": len(mailto_emails), "source": "homepage"})
            
            discovered_links = _discover_contact_links(html, base_url)
            
            social = extract_social_links(html)
            all_social.update(social)
            
            emails = _extract_emails_from_html(html, domain)
            all_emails.extend(emails)
            
            phones = _extract_phones_from_html(html)
            all_phones.extend(phones)
            
            break
        time.sleep(0.3)
    
    if not working_base:
        working_base = base_urls[0]
    
    if not all_emails:
        for path in CONTACT_PAGE_PATHS:
            url = urljoin(working_base, path)
            pages_tried += 1
            
            html = _fetch_page(url)
            if html:
                pages_success += 1
                
                mailto_emails = _extract_mailto_emails(html)
                all_emails.extend(mailto_emails)
                
                emails = _extract_emails_from_html(html, domain)
                all_emails.extend(emails)
                
                phones = _extract_phones_from_html(html)
                all_phones.extend(phones)
                
                social = extract_social_links(html)
                all_social.update(social)
                
                if all_emails:
                    log_enrichment("found_on_path", domain=domain, source="scrape",
                                   details={"path": path, "emails": len(all_emails)})
                    break
            
            time.sleep(0.3)
    
    if not all_emails and discovered_links:
        log_enrichment("following_discovered", domain=domain, source="scrape",
                       details={"links_count": len(discovered_links)})
        
        for link_url in discovered_links[:5]:
            pages_tried += 1
            html = _fetch_page(link_url)
            if html:
                pages_success += 1
                
                mailto_emails = _extract_mailto_emails(html)
                all_emails.extend(mailto_emails)
                
                emails = _extract_emails_from_html(html, domain)
                all_emails.extend(emails)
                
                phones = _extract_phones_from_html(html)
                all_phones.extend(phones)
                
                if all_emails:
                    log_enrichment("found_via_discovery", domain=domain, source="scrape",
                                   details={"url": link_url[:50]})
                    break
            
            time.sleep(0.3)
    
    all_emails = list(dict.fromkeys(all_emails))
    
    skip_patterns = [
        "example.com", "domain.com", "email.com", "yoursite.com",
        "placeholder", "test@", "noreply", "no-reply", 
        ".png", ".jpg", ".gif", ".svg", ".webp",
        "wixpress.com", "sentry.io", "cloudflare", "google.com",
        "facebook.com", "twitter.com", "schema.org"
    ]
    all_emails = [e for e in all_emails if not any(skip in e.lower() for skip in skip_patterns)]
    
    domain_root = domain.replace("www.", "").split(".")[0].lower()
    domain_emails = [e for e in all_emails if domain_root in e.lower() or domain in e.lower()]
    generic_good = [e for e in all_emails if any(p in e.lower() for p in ["info@", "contact@", "hello@", "sales@", "support@", "admin@", "office@", "team@", "mail@", "enquiries@", "inquiries@"])]
    other_emails = [e for e in all_emails if e not in domain_emails and e not in generic_good]
    
    all_emails = domain_emails + generic_good + other_emails
    all_emails = all_emails[:5]
    
    all_phones = list(dict.fromkeys(all_phones))[:3]
    
    if not all_emails:
        guessed_emails = _guess_domain_emails(domain)
        if guessed_emails:
            log_enrichment("guessed_emails", domain=domain, source="scrape",
                           details={"guessed": guessed_emails[:3]})
            all_emails = guessed_emails[:3]
    
    if not all_emails and not all_phones and not all_social:
        log_enrichment("no_data", domain=domain, source="scrape",
                       details={"pages_tried": pages_tried, "pages_success": pages_success,
                                "discovered_links": len(discovered_links)})
        return None
    
    result: Dict[str, Any] = {"source": "scrape"}
    
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
                       "pages_tried": pages_tried,
                       "pages_success": pages_success
                   })
    
    return result


USELESS_DOMAINS = [
    "news.google.com", "google.com", "reddit.com", "facebook.com",
    "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "youtube.com", "tiktok.com", "yelp.com", "bbb.org",
    "bizjournals.com", "prnewswire.com", "businesswire.com",
    "globenewswire.com", "reuters.com", "bloomberg.com",
    "yahoo.com", "msn.com", "cnn.com", "foxnews.com",
    "local10.com", "wsvn.com", "nbcmiami.com", "cbsmiami.com",
    "miamiherald.com", "sun-sentinel.com", "palmbeachpost.com",
]


def _extract_company_domain_from_name(company_name: str) -> Optional[str]:
    """Try to guess a domain from company name."""
    if not company_name:
        return None
    
    name = company_name.lower().strip()
    name = re.sub(r'\s+(inc|llc|corp|co|ltd|llp|pllc|pc|pa)\.?$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[^a-z0-9\s]', '', name)
    name = name.strip()
    
    if not name or len(name) < 2:
        return None
    
    slug = name.replace(' ', '')
    
    return f"{slug}.com"


def _get_domain_for_enrichment(lead_event: LeadEvent, session: Session) -> Optional[str]:
    """
    Smart domain extraction that avoids news/aggregator sites.
    
    Priority:
    1. lead_domain if it's a real company domain
    2. Lead.website if linked
    3. Guess from lead_company name
    4. Extract from summary text
    """
    if lead_event.lead_domain:
        domain = lead_event.lead_domain.lower().replace("www.", "")
        if domain and domain not in USELESS_DOMAINS and not any(u in domain for u in USELESS_DOMAINS):
            return domain
    
    if lead_event.lead_id:
        lead = session.exec(
            select(Lead).where(Lead.id == lead_event.lead_id)
        ).first()
        if lead and lead.website:
            domain = extract_domain_from_url(lead.website)
            if domain and domain not in USELESS_DOMAINS:
                return domain
    
    if lead_event.lead_company:
        guessed = _extract_company_domain_from_name(lead_event.lead_company)
        if guessed:
            return guessed
    
    if lead_event.summary:
        url_match = re.search(r"https?://(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+)", lead_event.summary)
        if url_match:
            domain = url_match.group(1).lower()
            if domain not in USELESS_DOMAINS:
                return domain
    
    return None


async def enrich_lead_event(lead_event: LeadEvent, session: Session) -> EnrichmentResult:
    """
    Main entry point for enriching a LeadEvent with contact information.
    
    Email-first approach: If lead_email is already set from signal extraction, skip scraping.
    Otherwise, uses smart domain extraction to find real company domains (not news sites).
    Then tries enrichment sources in order:
    1. Web scraping (always available, no API key needed)
    2. Hunter.io (if API key set)
    3. Clearbit (if API key set)
    
    Args:
        lead_event: LeadEvent to enrich
        session: Database session for Lead lookup
        
    Returns:
        EnrichmentResult with status and data
    """
    log_enrichment("start", lead_event_id=lead_event.id,
                   details={"company": lead_event.lead_company, "domain": lead_event.lead_domain, 
                             "has_email": bool(lead_event.lead_email)})
    
    if lead_event.lead_email:
        log_enrichment("email_first", lead_event_id=lead_event.id,
                       details={"email": lead_event.lead_email, "source": "signal"})
        return EnrichmentResult(
            success=True,
            source="signal",
            email=lead_event.lead_email
        )
    
    domain = _get_domain_for_enrichment(lead_event, session)
    
    if not domain:
        log_enrichment("skip", lead_event_id=lead_event.id,
                       details={"reason": "No usable domain", "lead_domain": lead_event.lead_domain})
        return EnrichmentResult(
            success=False,
            source="none",
            error="No usable domain available for enrichment"
        )
    
    result = EnrichmentResult(success=False, source="none")
    
    log_enrichment("scrape_first", domain=domain, lead_event_id=lead_event.id,
                   details={"strategy": "web_scrape_primary"})
    
    scrape_data = scrape_contact_page(domain)
    
    if scrape_data:
        if scrape_data.get("email"):
            result.email = scrape_data["email"]
            result.success = True
            result.source = "scrape"
        
        if scrape_data.get("phone"):
            result.phone = scrape_data["phone"]
        
        if scrape_data.get("social_links"):
            result.social_links = scrape_data["social_links"]
    
    if not result.email and HUNTER_API_KEY:
        time.sleep(RATE_LIMIT_DELAY)
        hunter_data = try_hunter_enrichment(domain)
        
        if hunter_data and hunter_data.get("email"):
            result.success = True
            result.source = "hunter"
            result.email = hunter_data.get("email")
            result.contact_name = hunter_data.get("contact_name")
            result.company_name = hunter_data.get("company_name")
            if hunter_data.get("social_links"):
                for platform, url in hunter_data["social_links"].items():
                    if platform not in result.social_links:
                        result.social_links[platform] = url
    
    if (not result.company_name or not result.social_links) and CLEARBIT_API_KEY:
        time.sleep(RATE_LIMIT_DELAY)
        clearbit_data = try_clearbit_enrichment(domain)
        
        if clearbit_data:
            if not result.company_name:
                result.company_name = clearbit_data.get("company_name")
            
            if clearbit_data.get("social_links"):
                for platform, url in clearbit_data["social_links"].items():
                    if platform not in result.social_links:
                        result.social_links[platform] = url
    
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


def _apply_phone_enrichment(lead_event: LeadEvent, session: Session) -> bool:
    """
    PHONESTORM: Apply phone enrichment to LeadEvent.
    
    Attempts to discover phone numbers from the lead's domain.
    Phone data is stored regardless of email status for future use.
    
    Args:
        lead_event: LeadEvent to update
        session: Database session
        
    Returns:
        True if phone was discovered, False otherwise
    """
    if not lead_event.lead_domain:
        return False
    
    if lead_event.lead_phone_e164:
        return True
    
    try:
        phone_result = discover_phones(lead_event.lead_domain)
        
        if phone_result.success and phone_result.best_phone:
            best = phone_result.best_phone
            lead_event.lead_phone_raw = best.raw_number
            lead_event.lead_phone_e164 = best.e164_number
            lead_event.phone_confidence = best.confidence
            lead_event.phone_source = best.source
            lead_event.phone_type = best.phone_type
            
            log_enrichment("PHONESTORM_FOUND", lead_event_id=lead_event.id,
                           details={
                               "phone": best.e164_number,
                               "confidence": best.confidence,
                               "source": best.source,
                               "phone_type": best.phone_type
                           })
            
            return True
        else:
            log_enrichment("PHONESTORM_NOT_FOUND", lead_event_id=lead_event.id,
                           details={"domain": lead_event.lead_domain})
            return False
            
    except Exception as e:
        log_enrichment("PHONESTORM_ERROR", lead_event_id=lead_event.id,
                       error=str(e))
        return False


def _apply_enrichment_to_lead_event(
    lead_event: LeadEvent,
    result: EnrichmentResult,
    session: Session,
    domain_discovered: bool = False
) -> str:
    """
    Apply enrichment results to LeadEvent and persist to database.
    
    Uses new lifecycle states:
    - UNENRICHED: No domain discovered yet
    - WITH_DOMAIN_NO_EMAIL: Domain found but no email discovered  
    - WITH_PHONE_ONLY: Phone found but no email (PHONESTORM)
    - ENRICHED_NO_OUTBOUND: Email found, awaiting outbound
    - OUTBOUND_SENT: Outbound email sent (set by BizDev cycle)
    
    Args:
        lead_event: LeadEvent to update
        result: EnrichmentResult with data
        session: Database session
        domain_discovered: True if domain was just discovered this cycle
        
    Returns:
        New enrichment status string
    """
    lead_event.enrichment_attempts = (lead_event.enrichment_attempts or 0) + 1
    lead_event.last_enrichment_at = datetime.utcnow()
    
    has_phone = _apply_phone_enrichment(lead_event, session)
    
    if result.success and result.email:
        lead_event.enrichment_status = ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND
        lead_event.enrichment_source = result.source
        lead_event.enriched_email = result.email
        lead_event.enriched_phone = result.phone
        lead_event.enriched_contact_name = result.contact_name
        lead_event.enriched_company_name = result.company_name
        lead_event.enriched_social_links = json.dumps(result.social_links) if result.social_links else None
        lead_event.enriched_at = datetime.utcnow()
        
        # ARCHANGEL: Set email confidence from result
        lead_event.email_confidence = result.email_confidence if result.email_confidence > 0 else 0.75
        
        if not lead_event.lead_email:
            lead_event.lead_email = result.email
            log_enrichment("ARCHANGEL_EMAIL_SET", lead_event_id=lead_event.id,
                           details={"lead_email": result.email, "source": result.source, 
                                    "email_confidence": lead_event.email_confidence})
        
        if result.contact_name and not lead_event.lead_name:
            lead_event.lead_name = result.contact_name
            
        log_enrichment("ARCHANGEL_STATUS_ENRICHED", lead_event_id=lead_event.id,
                       details={"new_status": ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND,
                                "email_confidence": lead_event.email_confidence})
        
        session.add(lead_event)
        session.commit()
        
        send_result = send_lead_event_immediate(session, lead_event, commit=True)
        log_enrichment("ARCHANGEL_IMMEDIATE_SEND", lead_event_id=lead_event.id,
                       details={"action": send_result.action, "success": send_result.success,
                                "reason": send_result.reason})
        
        return lead_event.enrichment_status
    
    elif lead_event.lead_domain:
        if has_phone and lead_event.lead_phone_e164:
            lead_event.enrichment_status = ENRICHMENT_STATUS_WITH_PHONE_ONLY
            lead_event.enrichment_source = result.source if result else "phonestorm"
            log_enrichment("PHONESTORM_STATUS_WITH_PHONE", lead_event_id=lead_event.id,
                           details={"new_status": ENRICHMENT_STATUS_WITH_PHONE_ONLY,
                                    "domain": lead_event.lead_domain,
                                    "phone": lead_event.lead_phone_e164,
                                    "phone_confidence": lead_event.phone_confidence})
        else:
            lead_event.enrichment_status = ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL
            lead_event.enrichment_source = result.source if result else "none"
            log_enrichment("ARCHANGEL_STATUS_TRANSITION", lead_event_id=lead_event.id,
                           details={"new_status": ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL, 
                                    "domain": lead_event.lead_domain,
                                    "domain_confidence": lead_event.domain_confidence})
    
    else:
        lead_event.enrichment_status = ENRICHMENT_STATUS_UNENRICHED
        log_enrichment("status_transition", lead_event_id=lead_event.id,
                       details={"new_status": ENRICHMENT_STATUS_UNENRICHED, "reason": "no_domain"})
    
    session.add(lead_event)
    session.commit()
    
    return lead_event.enrichment_status


MAX_ENRICHMENT_PER_CYCLE = int(os.environ.get("MAX_ENRICHMENT_PER_CYCLE", "25"))


def _get_source_url_for_event(lead_event: LeadEvent, session: Session) -> Optional[str]:
    """Get source URL from the Signal associated with a LeadEvent."""
    if not lead_event.signal_id:
        return None
    
    signal = session.exec(
        select(Signal).where(Signal.id == lead_event.signal_id)
    ).first()
    
    if not signal or not signal.raw_payload:
        return None
    
    try:
        payload = json.loads(signal.raw_payload)
        return payload.get("url") or payload.get("source_url") or payload.get("link")
    except (json.JSONDecodeError, TypeError):
        return None


async def run_enrichment_pipeline(session: Session, max_events: Optional[int] = None) -> dict:
    """
    Domain-first enrichment pipeline for LeadEvents with PHONESTORM integration.
    
    Three-phase pipeline:
    1. Domain Discovery: For UNENRICHED events, attempt to discover a domain
    2. Phone Extraction: During enrichment, extract phone numbers (PHONESTORM)
    3. Email Enrichment: For WITH_DOMAIN_NO_EMAIL/WITH_PHONE_ONLY events, scrape for emails
    
    State transitions:
    - UNENRICHED + domain found → WITH_DOMAIN_NO_EMAIL or WITH_PHONE_ONLY
    - WITH_DOMAIN_NO_EMAIL/WITH_PHONE_ONLY + email found → ENRICHED_NO_OUTBOUND
    - ENRICHED_NO_OUTBOUND → OUTBOUND_SENT (by BizDev cycle)
    
    PHONESTORM: Phone numbers are extracted alongside email enrichment.
    WITH_PHONE_ONLY leads have phone but no email - prioritize for retry.
    
    Args:
        session: Database session
        max_events: Maximum events to process per cycle
        
    Returns:
        Summary dict with enrichment stats
    """
    if max_events is None:
        max_events = MAX_ENRICHMENT_PER_CYCLE
        
    log_enrichment("pipeline_start", details={"status": "starting", "max_events": max_events})
    
    unenriched_events = list(session.exec(
        select(LeadEvent)
        .where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_UNENRICHED)
        .order_by(LeadEvent.created_at.desc())
        .limit(max_events // 2)
    ).all())
    
    with_domain_events = list(session.exec(
        select(LeadEvent)
        .where(LeadEvent.enrichment_status.in_([
            ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL,
            ENRICHMENT_STATUS_WITH_PHONE_ONLY
        ]))
        .order_by(LeadEvent.created_at.desc())
        .limit(max_events // 2)
    ).all())
    
    legacy_events = session.exec(
        select(LeadEvent)
        .where(LeadEvent.enrichment_status.in_([
            ENRICHMENT_STATUS_SKIPPED, 
            ENRICHMENT_STATUS_FAILED,
            ENRICHMENT_STATUS_ENRICHED
        ]))
        .limit(max_events // 4)
    ).all()
    
    for le in legacy_events:
        if le.enrichment_status in [ENRICHMENT_STATUS_SKIPPED, ENRICHMENT_STATUS_FAILED]:
            le.enrichment_status = ENRICHMENT_STATUS_UNENRICHED
        elif le.enrichment_status == ENRICHMENT_STATUS_ENRICHED:
            if le.lead_email:
                le.enrichment_status = ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND
            elif le.lead_domain:
                le.enrichment_status = ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL
            else:
                le.enrichment_status = ENRICHMENT_STATUS_UNENRICHED
        session.add(le)
    if legacy_events:
        session.commit()
        log_enrichment("legacy_migration", details={"migrated": len(legacy_events)})
    
    total_unenriched = len(session.exec(
        select(LeadEvent).where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_UNENRICHED)
    ).all())
    
    total_with_domain = len(session.exec(
        select(LeadEvent).where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL)
    ).all())
    
    log_enrichment("pipeline_load", details={
        "unenriched_batch": len(unenriched_events),
        "with_domain_batch": len(with_domain_events),
        "total_unenriched": total_unenriched,
        "total_with_domain": total_with_domain
    })
    
    total_with_phone = len(session.exec(
        select(LeadEvent).where(LeadEvent.enrichment_status == ENRICHMENT_STATUS_WITH_PHONE_ONLY)
    ).all())
    
    stats = {
        "processed": 0,
        "domains_discovered": 0,
        "phones_discovered": 0,
        "enriched": 0,
        "with_domain_no_email": 0,
        "with_phone_only": 0,
        "still_unenriched": 0,
        "pending_unenriched": total_unenriched - len(unenriched_events),
        "pending_with_domain": total_with_domain - len(with_domain_events),
        "pending_with_phone": total_with_phone,
        "by_source": {
            "domain_discovery": 0,
            "scrape": 0,
            "phonestorm": 0,
            "signal": 0,
            "none": 0
        }
    }
    
    for i, lead_event in enumerate(unenriched_events):
        source_url = _get_source_url_for_event(lead_event, session)
        
        domain_result = discover_domain_for_lead_event(
            lead_event_id=lead_event.id,
            lead_domain=lead_event.lead_domain,
            lead_email=lead_event.lead_email,
            lead_company=lead_event.lead_company,
            source_url=source_url,
            geography=None,
            niche=None,
            summary=lead_event.summary
        )
        
        stats["processed"] += 1
        
        if domain_result.success and domain_result.domain:
            lead_event.lead_domain = domain_result.domain
            lead_event.enrichment_status = ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL
            lead_event.enrichment_source = domain_result.source
            lead_event.last_enrichment_at = datetime.utcnow()
            lead_event.domain_confidence = domain_result.confidence
            
            # ARCHANGEL: Extract and store company name candidate
            if not lead_event.company_name_candidate:
                lead_event.company_name_candidate = extract_company_name_from_summary(lead_event.summary)
            
            session.add(lead_event)
            session.commit()
            
            stats["domains_discovered"] += 1
            stats["by_source"]["domain_discovery"] += 1
            
            log_enrichment("ARCHANGEL_DOMAIN_DISCOVERED", lead_event_id=lead_event.id,
                           domain=domain_result.domain,
                           details={"method": domain_result.discovery_method, 
                                    "confidence": domain_result.confidence,
                                    "company_candidate": lead_event.company_name_candidate})
            
            with_domain_events.append(lead_event)
        else:
            lead_event.enrichment_attempts = (lead_event.enrichment_attempts or 0) + 1
            lead_event.last_enrichment_at = datetime.utcnow()
            session.add(lead_event)
            session.commit()
            
            stats["still_unenriched"] += 1
            stats["by_source"]["none"] += 1
        
        if i < len(unenriched_events) - 1:
            await asyncio.sleep(0.5)
    
    for i, lead_event in enumerate(with_domain_events):
        if lead_event.lead_email:
            lead_event.enrichment_status = ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND
            lead_event.email_confidence = 0.95 if "@" in lead_event.lead_email else 0.0
            session.add(lead_event)
            session.commit()
            
            send_result = send_lead_event_immediate(session, lead_event, commit=True)
            
            stats["enriched"] += 1
            stats["by_source"]["signal"] += 1
            if send_result.email_sent:
                stats["immediate_sent"] = stats.get("immediate_sent", 0) + 1
            elif send_result.queued_for_review:
                stats["immediate_queued"] = stats.get("immediate_queued", 0) + 1
            
            log_enrichment("ARCHANGEL_IMMEDIATE_SEND", lead_event_id=lead_event.id,
                           details={"email": lead_event.lead_email,
                                    "action": send_result.action, 
                                    "success": send_result.success,
                                    "reason": send_result.reason})
            continue
        
        result = await enrich_lead_event(lead_event, session)
        stats["processed"] += 1
        
        new_status = _apply_enrichment_to_lead_event(lead_event, result, session, domain_discovered=False)
        
        if new_status == ENRICHMENT_STATUS_ENRICHED_NO_OUTBOUND:
            stats["enriched"] += 1
            stats["by_source"]["scrape"] += 1
        else:
            stats["with_domain_no_email"] += 1
        
        if (i + 1) % 5 == 0:
            log_enrichment("pipeline_progress", details={
                "phase": "email_enrichment",
                "processed": i + 1,
                "enriched": stats["enriched"]
            })
        
        if i < len(with_domain_events) - 1:
            await asyncio.sleep(RATE_LIMIT_DELAY)
    
    archival_result = archive_stale_leads(session, max_to_archive=25)
    stats["archived"] = archival_result.get("archived", 0)
    
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


STALE_LEAD_AGE_DAYS = int(os.environ.get("STALE_LEAD_AGE_DAYS", "30"))


def archive_stale_leads(session: Session, max_to_archive: int = 50) -> dict:
    """
    Archive LeadEvents that have been stuck in non-actionable states for too long.
    
    Criteria for archival:
    - Status is UNENRICHED or WITH_DOMAIN_NO_EMAIL (non-actionable)
    - Created more than STALE_LEAD_AGE_DAYS days ago (default: 30)
    
    This prevents the pipeline from repeatedly trying to enrich stale leads
    that are unlikely to ever be enriched successfully.
    
    Args:
        session: Database session
        max_to_archive: Maximum leads to archive per call
        
    Returns:
        Dict with archival stats
    """
    from datetime import timedelta
    
    cutoff_date = datetime.utcnow() - timedelta(days=STALE_LEAD_AGE_DAYS)
    
    stale_events = session.exec(
        select(LeadEvent)
        .where(LeadEvent.enrichment_status.in_([
            ENRICHMENT_STATUS_UNENRICHED,
            ENRICHMENT_STATUS_WITH_DOMAIN_NO_EMAIL
        ]))
        .where(LeadEvent.created_at < cutoff_date)
        .order_by(LeadEvent.created_at.asc())
        .limit(max_to_archive)
    ).all()
    
    if not stale_events:
        return {"archived": 0, "message": "No stale leads to archive"}
    
    archived_count = 0
    archived_by_status = {"UNENRICHED": 0, "WITH_DOMAIN_NO_EMAIL": 0}
    
    for event in stale_events:
        old_status = event.enrichment_status
        event.enrichment_status = ENRICHMENT_STATUS_ARCHIVED
        event.last_enrichment_at = datetime.utcnow()
        session.add(event)
        
        if old_status == ENRICHMENT_STATUS_UNENRICHED:
            archived_by_status["UNENRICHED"] += 1
        else:
            archived_by_status["WITH_DOMAIN_NO_EMAIL"] += 1
        
        archived_count += 1
    
    session.commit()
    
    log_enrichment("stale_leads_archived", details={
        "archived": archived_count,
        "cutoff_days": STALE_LEAD_AGE_DAYS,
        "by_status": archived_by_status
    })
    
    return {
        "archived": archived_count,
        "cutoff_days": STALE_LEAD_AGE_DAYS,
        "by_status": archived_by_status,
        "message": f"Archived {archived_count} stale leads (>{STALE_LEAD_AGE_DAYS} days old)"
    }


print(f"{_get_dry_run_prefix()}[ENRICHMENT][STARTUP] Hunter: {'enabled' if HUNTER_API_KEY else 'disabled'}, Clearbit: {'enabled' if CLEARBIT_API_KEY else 'disabled'}, DRY_RUN: {ENRICHMENT_DRY_RUN}")
