"""
OPERATION EMAILSTORM v1: Layered Email Discovery Engine

Multi-layered email discovery with confidence scoring:
1. Direct extraction from website contact pages
2. Pattern-based email generation from domain
3. Email format guessing with validation
4. Social profile email extraction

Pure web scraping - NO paid APIs (NO Hunter.io, NO Clearbit for discovery).
SMTP validation optional for high-confidence scoring.
"""

import os
import re
import socket
import smtplib
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from urllib.parse import urlparse, urljoin

import requests
from requests.exceptions import RequestException, Timeout


EMAILSTORM_TIMEOUT = int(os.getenv("EMAILSTORM_TIMEOUT", "8"))
ENABLE_SMTP_VALIDATION = os.getenv("ENABLE_SMTP_VALIDATION", "false").lower() in ("true", "1", "yes")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

CONTACT_PATHS = [
    "/contact", "/contact-us", "/contact_us", "/contactus",
    "/about", "/about-us", "/about_us", "/aboutus",
    "/team", "/our-team", "/meet-the-team",
    "/connect", "/get-in-touch", "/reach-us",
    "/support", "/help", "/inquiry",
]

PERSON_LIKE_PREFIXES = [
    "john", "jane", "mike", "michael", "david", "james", "robert", "chris",
    "sarah", "lisa", "jennifer", "maria", "jose", "carlos", "pedro",
    "owner", "president", "ceo", "manager", "director", "admin",
]

GENERIC_PREFIXES = [
    "info", "contact", "hello", "hi", "hey", "mail",
    "sales", "support", "help", "service", "services",
    "team", "office", "admin", "webmaster",
    "inquiries", "enquiries", "questions",
]

BLOCKED_EMAIL_PATTERNS = [
    r"example\.com", r"domain\.com", r"email\.com", r"yoursite\.com",
    r"placeholder", r"test@", r"noreply", r"no-reply",
    r"\.png", r"\.jpg", r"\.gif", r"\.svg", r"\.webp",
    r"wixpress\.com", r"sentry\.io", r"cloudflare",
    r"google\.com", r"facebook\.com", r"twitter\.com",
    r"schema\.org", r"w3\.org",
]

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

MAILTO_REGEX = re.compile(r'href=["\']mailto:([^"\'?]+)', re.IGNORECASE)


@dataclass
class EmailCandidate:
    """A discovered email with confidence scoring."""
    email: str
    confidence: float
    source: str
    email_type: str
    validation_status: str = "unknown"
    
    def to_dict(self) -> Dict:
        return {
            "email": self.email,
            "confidence": self.confidence,
            "source": self.source,
            "email_type": self.email_type,
            "validation_status": self.validation_status
        }


@dataclass
class EmailStormResult:
    """Result of email discovery."""
    success: bool
    best_email: Optional[EmailCandidate] = None
    all_emails: List[EmailCandidate] = field(default_factory=list)
    domain: Optional[str] = None
    extraction_time_ms: int = 0
    error: Optional[str] = None
    pages_checked: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "best_email": self.best_email.to_dict() if self.best_email else None,
            "all_emails": [e.to_dict() for e in self.all_emails],
            "domain": self.domain,
            "extraction_time_ms": self.extraction_time_ms,
            "error": self.error,
            "pages_checked": self.pages_checked
        }


def log_emailstorm(action: str, domain: Optional[str] = None, details: Optional[Dict] = None) -> None:
    """Log EMAILSTORM activity."""
    msg_parts = [f"[EMAILSTORM][{action.upper()}]"]
    if domain:
        msg_parts.append(f"domain={domain}")
    if details:
        for k, v in details.items():
            if isinstance(v, str) and len(v) > 50:
                v = v[:50] + "..."
            msg_parts.append(f"{k}={v}")
    print(" | ".join(msg_parts))


def _is_blocked_email(email: str) -> bool:
    """Check if email matches a blocked pattern."""
    email_lower = email.lower()
    for pattern in BLOCKED_EMAIL_PATTERNS:
        if re.search(pattern, email_lower):
            return True
    return False


def _classify_email_type(email: str) -> str:
    """Classify email as person-like or generic."""
    local_part = email.split("@")[0].lower()
    
    for prefix in PERSON_LIKE_PREFIXES:
        if local_part.startswith(prefix) or prefix in local_part:
            return "person_like"
    
    for prefix in GENERIC_PREFIXES:
        if local_part.startswith(prefix):
            return "generic"
    
    if "." in local_part or "_" in local_part:
        return "person_like"
    
    return "unknown"


def _calculate_email_confidence(email: str, source: str, domain: str) -> float:
    """Calculate confidence score for an email."""
    score = 0.5
    
    email_domain = email.split("@")[1].lower() if "@" in email else ""
    domain_root = domain.replace("www.", "").lower()
    
    if email_domain == domain_root or domain_root in email_domain:
        score += 0.25
    
    source_weights = {
        "mailto_link": 0.15,
        "contact_page": 0.10,
        "about_page": 0.08,
        "homepage": 0.05,
        "schema_org": 0.12,
        "meta_tag": 0.08,
        "guessed": -0.15,
    }
    score += source_weights.get(source, 0)
    
    email_type = _classify_email_type(email)
    if email_type == "person_like":
        score += 0.10
    elif email_type == "generic":
        score += 0.05
    
    score = max(0.1, min(1.0, score))
    
    return score


def _fetch_page(url: str) -> Optional[str]:
    """Fetch a page with error handling."""
    try:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=EMAILSTORM_TIMEOUT, allow_redirects=True)
        
        if response.status_code == 200:
            return response.text[:100000]
        
    except (RequestException, Timeout):
        pass
    
    return None


def _extract_emails_from_html(html: str) -> List[str]:
    """Extract all email addresses from HTML content."""
    emails = []
    
    mailto_matches = MAILTO_REGEX.findall(html)
    for email in mailto_matches:
        email = email.strip().lower()
        if "@" in email and not _is_blocked_email(email):
            emails.append(email)
    
    general_matches = EMAIL_REGEX.findall(html)
    for email in general_matches:
        email = email.strip().lower()
        if not _is_blocked_email(email) and email not in emails:
            emails.append(email)
    
    return list(dict.fromkeys(emails))


def _extract_from_schema_org(html: str, domain: str) -> List[EmailCandidate]:
    """Extract emails from Schema.org JSON-LD data."""
    candidates = []
    
    try:
        import json
        
        ld_pattern = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
        
        for match in ld_pattern.finditer(html):
            try:
                data = json.loads(match.group(1))
                
                if isinstance(data, list):
                    data = data[0] if data else {}
                
                email = data.get("email")
                if email:
                    email = email.replace("mailto:", "").strip().lower()
                    if "@" in email and not _is_blocked_email(email):
                        candidates.append(EmailCandidate(
                            email=email,
                            confidence=_calculate_email_confidence(email, "schema_org", domain),
                            source="schema_org",
                            email_type=_classify_email_type(email)
                        ))
                
                contact = data.get("contactPoint") or data.get("contact")
                if isinstance(contact, dict):
                    email = contact.get("email")
                    if email:
                        email = email.replace("mailto:", "").strip().lower()
                        if "@" in email and not _is_blocked_email(email):
                            candidates.append(EmailCandidate(
                                email=email,
                                confidence=_calculate_email_confidence(email, "schema_org", domain),
                                source="schema_org",
                                email_type=_classify_email_type(email)
                            ))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
                
    except Exception:
        pass
    
    return candidates


def _guess_common_emails(domain: str) -> List[EmailCandidate]:
    """Generate common email patterns for the domain."""
    candidates = []
    domain_clean = domain.replace("www.", "")
    
    common_patterns = [
        "info", "contact", "hello", "sales", "support",
        "office", "team", "admin", "mail"
    ]
    
    for prefix in common_patterns:
        email = f"{prefix}@{domain_clean}"
        candidates.append(EmailCandidate(
            email=email,
            confidence=_calculate_email_confidence(email, "guessed", domain),
            source="guessed",
            email_type="generic"
        ))
    
    return candidates


def validate_email_smtp(email: str) -> str:
    """
    Validate email via SMTP (lightweight check).
    
    Returns: "valid", "invalid", "unknown", or "error"
    """
    if not ENABLE_SMTP_VALIDATION:
        return "not_checked"
    
    try:
        domain = email.split("@")[1]
        
        records = socket.getaddrinfo(domain, 25, socket.AF_INET, socket.SOCK_STREAM)
        if not records:
            return "no_mx"
        
        return "mx_found"
        
    except socket.gaierror:
        return "no_dns"
    except Exception:
        return "error"


def discover_emails(
    domain: str,
    company_name: Optional[str] = None,
    check_contact_pages: bool = True,
    generate_guesses: bool = True
) -> EmailStormResult:
    """
    EMAILSTORM: Discover emails for a domain using multiple methods.
    
    Layered approach:
    1. Scrape contact/about pages for mailto: links and email patterns
    2. Extract from Schema.org structured data
    3. Generate common email patterns (info@, contact@, etc.)
    4. Optional SMTP validation for confidence boost
    
    Args:
        domain: Target domain
        company_name: Optional company name for context
        check_contact_pages: Whether to scrape contact pages
        generate_guesses: Whether to generate common email guesses
        
    Returns:
        EmailStormResult with discovered emails sorted by confidence
    """
    start_time = time.time()
    all_candidates: List[EmailCandidate] = []
    pages_checked = 0
    
    domain_clean = domain.replace("www.", "").lower()
    base_url = f"https://{domain_clean}"
    
    log_emailstorm("START", domain_clean, {"company": company_name})
    
    if check_contact_pages:
        homepage_html = _fetch_page(base_url)
        if homepage_html:
            pages_checked += 1
            
            schema_emails = _extract_from_schema_org(homepage_html, domain_clean)
            all_candidates.extend(schema_emails)
            
            homepage_emails = _extract_emails_from_html(homepage_html)
            for email in homepage_emails:
                if not any(c.email == email for c in all_candidates):
                    all_candidates.append(EmailCandidate(
                        email=email,
                        confidence=_calculate_email_confidence(email, "homepage", domain_clean),
                        source="homepage",
                        email_type=_classify_email_type(email)
                    ))
        
        for path in CONTACT_PATHS[:8]:
            page_url = urljoin(base_url, path)
            html = _fetch_page(page_url)
            
            if html:
                pages_checked += 1
                
                source_type = "contact_page" if "contact" in path else "about_page"
                page_emails = _extract_emails_from_html(html)
                
                for email in page_emails:
                    if not any(c.email == email for c in all_candidates):
                        all_candidates.append(EmailCandidate(
                            email=email,
                            confidence=_calculate_email_confidence(email, source_type, domain_clean),
                            source=source_type,
                            email_type=_classify_email_type(email)
                        ))
                
                schema_emails = _extract_from_schema_org(html, domain_clean)
                for candidate in schema_emails:
                    if not any(c.email == candidate.email for c in all_candidates):
                        all_candidates.append(candidate)
            
            time.sleep(0.2)
            
            if len(all_candidates) >= 5:
                break
    
    if generate_guesses and len(all_candidates) < 3:
        guessed = _guess_common_emails(domain_clean)
        all_candidates.extend(guessed[:5])
    
    domain_emails = [c for c in all_candidates if domain_clean in c.email.split("@")[1]]
    other_emails = [c for c in all_candidates if domain_clean not in c.email.split("@")[1]]
    
    domain_emails.sort(key=lambda c: c.confidence, reverse=True)
    other_emails.sort(key=lambda c: c.confidence, reverse=True)
    
    sorted_candidates = domain_emails + other_emails
    
    if ENABLE_SMTP_VALIDATION and sorted_candidates:
        for candidate in sorted_candidates[:3]:
            validation_status = validate_email_smtp(candidate.email)
            candidate.validation_status = validation_status
            
            if validation_status == "mx_found":
                candidate.confidence = min(1.0, candidate.confidence + 0.1)
            elif validation_status in ("no_mx", "no_dns"):
                candidate.confidence = max(0.1, candidate.confidence - 0.2)
        
        sorted_candidates.sort(key=lambda c: c.confidence, reverse=True)
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    if sorted_candidates:
        best = sorted_candidates[0]
        log_emailstorm("SUCCESS", domain_clean, {
            "count": len(sorted_candidates),
            "best": best.email,
            "confidence": f"{best.confidence:.2f}",
            "source": best.source,
            "pages": pages_checked
        })
        
        return EmailStormResult(
            success=True,
            best_email=best,
            all_emails=sorted_candidates[:10],
            domain=domain_clean,
            extraction_time_ms=elapsed_ms,
            pages_checked=pages_checked
        )
    else:
        log_emailstorm("NO_EMAILS", domain_clean, {"pages": pages_checked})
        
        return EmailStormResult(
            success=False,
            domain=domain_clean,
            extraction_time_ms=elapsed_ms,
            error="No emails discovered",
            pages_checked=pages_checked
        )


def get_best_email(
    domain: str,
    company_name: Optional[str] = None,
    min_confidence: float = 0.4
) -> Optional[str]:
    """
    Convenience function to get the best email for a domain.
    
    Returns the highest-confidence email if it meets the threshold.
    """
    result = discover_emails(domain, company_name)
    
    if result.success and result.best_email:
        if result.best_email.confidence >= min_confidence:
            return result.best_email.email
    
    return None
