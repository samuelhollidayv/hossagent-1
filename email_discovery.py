"""
Autonomous Email Discovery Module

Scrapes company websites to find contact email addresses without using paid APIs.
This module provides a fallback email discovery mechanism when Hunter.io and 
Clearbit are disabled.

Strategy:
1. Find contact/about pages on the company website
2. Extract email addresses using regex patterns
3. Validate and score discovered emails
4. Prefer business emails over generic ones (info@, contact@, hello@)

Rate limiting and polite scraping practices are enforced.
"""

import os
import re
import time
import json
import random
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Set
from urllib.parse import urljoin, urlparse
import requests
from requests.exceptions import RequestException, Timeout


DISCOVERY_TIMEOUT = int(os.getenv("EMAIL_DISCOVERY_TIMEOUT", "10"))
DISCOVERY_MAX_PAGES = int(os.getenv("EMAIL_DISCOVERY_MAX_PAGES", "5"))
DISCOVERY_DELAY_MIN = float(os.getenv("EMAIL_DISCOVERY_DELAY_MIN", "1.0"))
DISCOVERY_DELAY_MAX = float(os.getenv("EMAIL_DISCOVERY_DELAY_MAX", "3.0"))
DISCOVERY_DRY_RUN = os.getenv("EMAIL_DISCOVERY_DRY_RUN", "false").lower() == "true"
DISCOVERY_ENABLED = os.getenv("EMAIL_DISCOVERY_ENABLED", "true").lower() == "true"

CONTACT_PAGE_PATTERNS = [
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/aboutus",
    "/team",
    "/our-team",
    "/staff",
    "/people",
    "/leadership",
    "/get-in-touch",
    "/reach-us",
    "/connect",
    "/info",
    "/company",
    "/support",
    "/help",
]

EMAIL_REGEX = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
)

MAILTO_REGEX = re.compile(
    r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})',
    re.IGNORECASE
)

GENERIC_EMAIL_PREFIXES = [
    "info", "contact", "hello", "support", "help", "sales", "enquiries",
    "inquiries", "admin", "office", "team", "general", "mail", "email",
    "customerservice", "customer-service", "customercare", "feedback",
    "service", "helpdesk", "noreply", "no-reply", "webmaster", "enquiry"
]

INVALID_EMAIL_PATTERNS = [
    r".*@example\.com$",
    r".*@test\.com$",
    r".*@localhost$",
    r".*@.*\.png$",
    r".*@.*\.jpg$",
    r".*@.*\.gif$",
    r".*\.wixpress\.com$",
    r".*sentry\.io$",
    r"noreply@.*",
    r"no-reply@.*",
    r"donotreply@.*",
    r"do-not-reply@.*",
    r"unsubscribe@.*",
    r"mailer-daemon@.*",
    r"postmaster@.*",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


@dataclass
class DiscoveredEmail:
    """Represents a discovered email with metadata."""
    email: str
    source_url: str
    confidence: float  
    is_generic: bool
    domain_match: bool  
    discovered_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict:
        return {
            "email": self.email,
            "source_url": self.source_url,
            "confidence": self.confidence,
            "is_generic": self.is_generic,
            "domain_match": self.domain_match,
            "discovered_at": self.discovered_at.isoformat()
        }


@dataclass
class DiscoveryResult:
    """Result of email discovery attempt."""
    success: bool
    domain: str
    emails: List[DiscoveredEmail] = field(default_factory=list)
    best_email: Optional[str] = None
    pages_checked: int = 0
    error: Optional[str] = None
    duration_ms: int = 0
    dry_run: bool = False
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "domain": self.domain,
            "emails": [e.to_dict() for e in self.emails],
            "best_email": self.best_email,
            "pages_checked": self.pages_checked,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "dry_run": self.dry_run
        }


_domain_cache: Dict[str, Tuple[DiscoveryResult, datetime]] = {}
CACHE_TTL_HOURS = 24


def _get_cached_result(domain: str) -> Optional[DiscoveryResult]:
    """Check cache for recent discovery result."""
    if domain in _domain_cache:
        result, cached_at = _domain_cache[domain]
        if datetime.utcnow() - cached_at < timedelta(hours=CACHE_TTL_HOURS):
            return result
        else:
            del _domain_cache[domain]
    return None


def _cache_result(domain: str, result: DiscoveryResult) -> None:
    """Cache discovery result."""
    _domain_cache[domain] = (result, datetime.utcnow())


def _get_random_user_agent() -> str:
    """Get a random user agent for requests."""
    return random.choice(USER_AGENTS)


def _polite_delay() -> None:
    """Apply a polite delay between requests."""
    delay = random.uniform(DISCOVERY_DELAY_MIN, DISCOVERY_DELAY_MAX)
    time.sleep(delay)


def _normalize_domain(domain: str) -> str:
    """Normalize domain to standard format."""
    domain = domain.lower().strip()
    if domain.startswith("http://") or domain.startswith("https://"):
        parsed = urlparse(domain)
        domain = parsed.netloc
    domain = domain.lstrip("www.")
    return domain


def _build_base_url(domain: str) -> str:
    """Build base URL from domain."""
    domain = _normalize_domain(domain)
    return f"https://{domain}"


def _is_valid_email(email: str, target_domain: str) -> bool:
    """Check if email is valid and not in blocklist."""
    email = email.lower().strip()
    
    for pattern in INVALID_EMAIL_PATTERNS:
        if re.match(pattern, email, re.IGNORECASE):
            return False
    
    if len(email) < 6 or len(email) > 254:
        return False
    
    if email.count("@") != 1:
        return False
    
    local, domain = email.rsplit("@", 1)
    if len(local) < 1 or len(domain) < 3:
        return False
    
    return True


def _is_generic_email(email: str) -> bool:
    """Check if email is a generic business email."""
    local = email.split("@")[0].lower()
    return any(local.startswith(prefix) or local == prefix for prefix in GENERIC_EMAIL_PREFIXES)


def _email_matches_domain(email: str, target_domain: str) -> bool:
    """Check if email domain matches target domain."""
    email_domain = email.split("@")[1].lower()
    target = _normalize_domain(target_domain)
    return email_domain == target or email_domain.endswith(f".{target}")


def classify_email(email: str) -> str:
    """
    ARCHANGEL: Classify email type - generic vs person-like.
    
    Returns: 'generic', 'person', 'other'
    """
    local = email.split("@")[0].lower()
    
    if _is_generic_email(email):
        return "generic"
    
    if "." in local or any(c.isupper() for c in local):
        return "person"
    
    return "other"


def _calculate_confidence(email: str, target_domain: str, source_url: str) -> float:
    """
    ARCHANGEL: Calculate confidence score for discovered email.
    
    Factors:
    - Domain match (30%)
    - Email pattern (20%)
    - Page context (10%)
    - Email type (40% bonus for person-like)
    """
    score = 0.5  
    
    if _email_matches_domain(email, target_domain):
        score += 0.3
    else:
        score -= 0.2
    
    email_type = classify_email(email)
    if email_type == "person":
        score += 0.4
    elif email_type == "generic":
        score += 0.1
    else:
        score += 0.0
    
    source_path = urlparse(source_url).path.lower()
    if any(pattern in source_path for pattern in ["/contact", "/about", "/team"]):
        score += 0.1
    
    return min(1.0, max(0.0, score))


def _extract_emails_from_html(html: str, target_domain: str) -> List[str]:
    """Extract email addresses from HTML content."""
    emails = set()
    
    mailto_matches = MAILTO_REGEX.findall(html)
    emails.update(mailto_matches)
    
    text_matches = EMAIL_REGEX.findall(html)
    emails.update(text_matches)
    
    valid_emails = []
    for email in emails:
        email = email.lower().strip()
        if _is_valid_email(email, target_domain):
            valid_emails.append(email)
    
    return list(set(valid_emails))


def _fetch_page(url: str) -> Optional[str]:
    """Fetch page content with error handling."""
    try:
        headers = {
            "User-Agent": _get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        
        response = requests.get(
            url,
            headers=headers,
            timeout=DISCOVERY_TIMEOUT,
            allow_redirects=True,
            verify=True
        )
        
        if response.status_code == 200:
            return response.text
        else:
            print(f"[EMAIL_DISCOVERY] HTTP {response.status_code} for {url}")
            return None
            
    except Timeout:
        print(f"[EMAIL_DISCOVERY] Timeout fetching {url}")
        return None
    except RequestException as e:
        print(f"[EMAIL_DISCOVERY] Request error for {url}: {str(e)[:100]}")
        return None


def _find_contact_pages(base_url: str, homepage_html: str) -> List[str]:
    """Find contact page URLs from homepage and common patterns."""
    contact_urls = []
    
    for pattern in CONTACT_PAGE_PATTERNS:
        contact_urls.append(urljoin(base_url, pattern))
    
    href_pattern = re.compile(r'href=["\']([^"\']*(?:contact|about|team)[^"\']*)["\']', re.IGNORECASE)
    matches = href_pattern.findall(homepage_html)
    for match in matches[:10]:  
        if match.startswith("http"):
            if urlparse(match).netloc == urlparse(base_url).netloc:
                contact_urls.append(match)
        elif match.startswith("/"):
            contact_urls.append(urljoin(base_url, match))
    
    seen = set()
    unique_urls = []
    for url in contact_urls:
        normalized = url.rstrip("/").lower()
        if normalized not in seen:
            seen.add(normalized)
            unique_urls.append(url)
    
    return unique_urls[:DISCOVERY_MAX_PAGES]


def discover_emails(domain: str) -> DiscoveryResult:
    """
    Discover email addresses from a company website.
    
    Args:
        domain: Company domain (e.g., "example.com")
        
    Returns:
        DiscoveryResult with found emails and metadata
    """
    start_time = time.time()
    domain = _normalize_domain(domain)
    
    if not DISCOVERY_ENABLED:
        return DiscoveryResult(
            success=False,
            domain=domain,
            error="Email discovery disabled",
            dry_run=True
        )
    
    cached = _get_cached_result(domain)
    if cached:
        print(f"[EMAIL_DISCOVERY] Cache hit for {domain}")
        return cached
    
    if DISCOVERY_DRY_RUN:
        print(f"[EMAIL_DISCOVERY][DRY_RUN] Would scrape {domain}")
        result = DiscoveryResult(
            success=False,
            domain=domain,
            error="Dry run mode - no actual scraping",
            dry_run=True
        )
        return result
    
    print(f"[EMAIL_DISCOVERY] Starting discovery for {domain}")
    
    base_url = _build_base_url(domain)
    all_emails: List[DiscoveredEmail] = []
    pages_checked = 0
    
    homepage_html = _fetch_page(base_url)
    if homepage_html:
        pages_checked += 1
        emails = _extract_emails_from_html(homepage_html, domain)
        for email in emails:
            all_emails.append(DiscoveredEmail(
                email=email,
                source_url=base_url,
                confidence=_calculate_confidence(email, domain, base_url),
                is_generic=_is_generic_email(email),
                domain_match=_email_matches_domain(email, domain)
            ))
        
        contact_urls = _find_contact_pages(base_url, homepage_html)
        
        for url in contact_urls:
            if pages_checked >= DISCOVERY_MAX_PAGES:
                break
            
            _polite_delay()
            
            page_html = _fetch_page(url)
            if page_html:
                pages_checked += 1
                emails = _extract_emails_from_html(page_html, domain)
                for email in emails:
                    if not any(e.email == email for e in all_emails):
                        all_emails.append(DiscoveredEmail(
                            email=email,
                            source_url=url,
                            confidence=_calculate_confidence(email, domain, url),
                            is_generic=_is_generic_email(email),
                            domain_match=_email_matches_domain(email, domain)
                        ))
    else:
        www_url = f"https://www.{domain}"
        homepage_html = _fetch_page(www_url)
        if homepage_html:
            pages_checked += 1
            emails = _extract_emails_from_html(homepage_html, domain)
            for email in emails:
                all_emails.append(DiscoveredEmail(
                    email=email,
                    source_url=www_url,
                    confidence=_calculate_confidence(email, domain, www_url),
                    is_generic=_is_generic_email(email),
                    domain_match=_email_matches_domain(email, domain)
                ))
    
    all_emails.sort(key=lambda e: (-e.confidence, e.is_generic))
    
    best_email = None
    if all_emails:
        domain_matches = [e for e in all_emails if e.domain_match]
        if domain_matches:
            personal = [e for e in domain_matches if not e.is_generic]
            if personal:
                best_email = personal[0].email
            else:
                best_email = domain_matches[0].email
        else:
            best_email = all_emails[0].email
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    result = DiscoveryResult(
        success=len(all_emails) > 0,
        domain=domain,
        emails=all_emails,
        best_email=best_email,
        pages_checked=pages_checked,
        duration_ms=duration_ms
    )
    
    _cache_result(domain, result)
    
    if result.success:
        print(f"[EMAIL_DISCOVERY] Found {len(all_emails)} email(s) for {domain}, best: {best_email}")
    else:
        print(f"[EMAIL_DISCOVERY] No emails found for {domain} (checked {pages_checked} pages)")
    
    return result


def discover_emails_batch(domains: List[str], max_concurrent: int = 1) -> Dict[str, DiscoveryResult]:
    """
    Discover emails for multiple domains.
    
    Args:
        domains: List of domains to check
        max_concurrent: Max concurrent requests (currently sequential for politeness)
        
    Returns:
        Dict mapping domain to DiscoveryResult
    """
    results = {}
    
    for domain in domains:
        results[domain] = discover_emails(domain)
        if domain != domains[-1]:  
            _polite_delay()
    
    return results


def get_discovery_status() -> Dict:
    """Get current email discovery configuration status."""
    return {
        "enabled": DISCOVERY_ENABLED,
        "dry_run": DISCOVERY_DRY_RUN,
        "timeout_seconds": DISCOVERY_TIMEOUT,
        "max_pages_per_domain": DISCOVERY_MAX_PAGES,
        "delay_range": f"{DISCOVERY_DELAY_MIN}-{DISCOVERY_DELAY_MAX}s",
        "cache_size": len(_domain_cache),
        "cache_ttl_hours": CACHE_TTL_HOURS
    }


if __name__ == "__main__":
    test_domains = ["hossagent.net", "google.com"]
    
    print("Email Discovery Test")
    print("=" * 50)
    print(f"Config: {get_discovery_status()}")
    print("=" * 50)
    
    for domain in test_domains:
        print(f"\nTesting: {domain}")
        result = discover_emails(domain)
        print(f"Result: {json.dumps(result.to_dict(), indent=2)}")
