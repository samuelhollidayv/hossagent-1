"""
Domain Discovery Module - Aggressive Domain Hunting for LeadEvents

Implements a layered, ruthless strategy to find company domains from signals.
NO external enrichment APIs (no Apollo, Hunter, Clearbit). Pure web intelligence.

Discovery Pipeline (in order):
1. Extract from existing fields (lead_domain, lead_email)
2. Extract from signal source_url (if company site vs news/directory)
3. Parse article pages for outbound links to company websites
4. Web search fallback (company name + geography + niche)

Guardrails:
- Reject social/directory/news domains
- Validate TLDs
- Match company name tokens to domain

Author: HossAgent
"""

import os
import re
import json
import time
import random
from datetime import datetime
from typing import Optional, List, Dict, Set, Tuple
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass, field

import requests
from requests.exceptions import RequestException, Timeout

DISCOVERY_TIMEOUT = int(os.getenv("DOMAIN_DISCOVERY_TIMEOUT", "10"))
DISCOVERY_DELAY_MIN = float(os.getenv("DOMAIN_DISCOVERY_DELAY_MIN", "0.5"))
DISCOVERY_DELAY_MAX = float(os.getenv("DOMAIN_DISCOVERY_DELAY_MAX", "1.5"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

BLOCKED_DOMAINS = {
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "tiktok.com", "pinterest.com",
    "yelp.com", "angi.com", "angieslist.com", "homeadvisor.com",
    "thumbtack.com", "houzz.com", "tripadvisor.com", "bbb.org",
    "google.com", "maps.google.com", "news.google.com", "bing.com",
    "yahoo.com", "msn.com", "aol.com",
    "reddit.com", "quora.com", "medium.com", "substack.com",
    "prnewswire.com", "businesswire.com", "globenewswire.com",
    "reuters.com", "bloomberg.com", "wsj.com", "nytimes.com",
    "cnn.com", "foxnews.com", "nbcnews.com", "cbsnews.com", "abcnews.com",
    "local10.com", "wsvn.com", "nbcmiami.com", "cbsmiami.com",
    "miamiherald.com", "sun-sentinel.com", "palmbeachpost.com",
    "southfloridabusinessjournal.com", "bizjournals.com",
    "wikipedia.org", "wikimedia.org",
    "amazon.com", "ebay.com", "etsy.com", "shopify.com",
    "craigslist.org", "nextdoor.com",
    "glassdoor.com", "indeed.com", "ziprecruiter.com",
    "patch.com", "axios.com", "huffpost.com",
    "wix.com", "squarespace.com", "godaddy.com", "wordpress.com",
    "mailchimp.com", "constantcontact.com",
}

NEWS_DOMAIN_PATTERNS = [
    r".*news.*\.com$", r".*herald.*\.com$", r".*times.*\.com$",
    r".*post.*\.com$", r".*tribune.*\.com$", r".*journal.*\.com$",
    r".*gazette.*\.com$", r".*observer.*\.com$", r".*daily.*\.com$",
    r".*weekly.*\.com$", r".*local\d+\.com$", r".*tv\.com$",
]

VALID_TLDS = {
    ".com", ".net", ".org", ".biz", ".co", ".io", ".us", ".info",
    ".pro", ".me", ".tv", ".cc", ".co.uk", ".ca", ".mx", ".br",
}


@dataclass
class DomainDiscoveryResult:
    """Result of domain discovery attempt - ARCHANGEL Discovery Engine."""
    success: bool
    domain: Optional[str] = None
    source: str = "none"
    confidence: float = 0.0
    company_name_match: bool = False
    discovery_method: str = ""
    attempts: int = 0
    error: Optional[str] = None
    company_name_candidate: Optional[str] = None  # ARCHANGEL: extracted company name
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "domain": self.domain,
            "source": self.source,
            "confidence": self.confidence,
            "company_name_match": self.company_name_match,
            "discovery_method": self.discovery_method,
            "attempts": self.attempts,
            "error": self.error,
        }


def log_discovery(
    action: str,
    lead_event_id: Optional[int] = None,
    domain: Optional[str] = None,
    details: Optional[Dict] = None,
    error: Optional[str] = None
) -> None:
    """Log domain discovery activity - ARCHANGEL logging."""
    msg_parts = [f"[ARCHANGEL][DOMAIN_DISCOVERY][{action.upper()}]"]
    if lead_event_id:
        msg_parts.append(f"event={lead_event_id}")
    if domain:
        msg_parts.append(f"domain={domain}")
    if details:
        for k, v in details.items():
            if isinstance(v, str) and len(v) > 50:
                v = v[:50] + "..."
            msg_parts.append(f"{k}={v}")
    if error:
        msg_parts.append(f"error={error}")
    print(" ".join(msg_parts))


def _get_random_user_agent() -> str:
    """Get a random user agent string."""
    return random.choice(USER_AGENTS)


def _normalize_domain(url_or_domain: str) -> Optional[str]:
    """
    Extract and normalize domain from URL or domain string.
    
    Returns lowercase domain without www prefix, or None if invalid.
    """
    if not url_or_domain:
        return None
    
    url_or_domain = url_or_domain.strip().lower()
    
    if url_or_domain.startswith("http"):
        try:
            parsed = urlparse(url_or_domain)
            domain = parsed.netloc
        except Exception:
            return None
    else:
        domain = url_or_domain.split("/")[0]
    
    domain = domain.replace("www.", "")
    
    domain = re.sub(r":\d+$", "", domain)
    
    if not domain or "." not in domain:
        return None
    
    return domain


def _is_blocked_domain(domain: str) -> bool:
    """Check if domain is in the blocked list or matches news patterns."""
    if not domain:
        return True
    
    domain_lower = domain.lower()
    
    if domain_lower in BLOCKED_DOMAINS:
        return True
    
    for blocked in BLOCKED_DOMAINS:
        if domain_lower.endswith("." + blocked) or blocked.endswith("." + domain_lower):
            return True
    
    for pattern in NEWS_DOMAIN_PATTERNS:
        if re.match(pattern, domain_lower, re.IGNORECASE):
            return True
    
    return False


def _has_valid_tld(domain: str) -> bool:
    """Check if domain has a valid TLD."""
    if not domain:
        return False
    
    for tld in VALID_TLDS:
        if domain.endswith(tld):
            return True
    
    parts = domain.split(".")
    if len(parts) >= 2:
        last_part = "." + parts[-1]
        if len(last_part) <= 4:
            return True
    
    return False


def extract_company_name_from_summary(summary: Optional[str]) -> Optional[str]:
    """
    ARCHANGEL: Extract probable company name from signal summary.
    
    STRICT heuristics - only extract if we have high confidence:
    - "News: Miami Best Roofing Announces..." -> "Miami Best Roofing"
    - "News: Cool Running Air Expands..." -> "Cool Running Air"
    - "News: Sunny Bliss Plumbing & Air Acquires..." -> "Sunny Bliss Plumbing & Air"
    
    REJECT generic industry descriptions:
    - "Texas HVAC company buys new..." -> None (generic, not branded)
    - "Owner of Orlando roofing company..." -> None (no specific name)
    - "Trump Supporter Breaks Down..." -> None (not a business)
    """
    if not summary:
        return None
    
    summary = summary.strip()
    
    quoted = re.search(r'"([^"]+)"', summary)
    if quoted:
        candidate = quoted.group(1).strip()
        if _is_valid_branded_company(candidate):
            return candidate
    
    text = summary
    if text.startswith("News: "):
        text = text[6:]
    
    action_verbs = r'(?:Announces?|Expands?|Acquires?|Opens?|Launches?|Reports?|Hires?|Receives?|Wins?|Partners?|Unveils?|Introduces?|Signs?|Adds?|Completes?|Celebrates?|Strengthens?)'
    
    business_nouns = r'(?:Air|Roofing|Plumbing|HVAC|Electric|Electrical|Landscaping|Construction|Realty|Properties|Solutions|Services|Partners|Group|Corp|Inc|LLC|Company|Co|Associates|Consulting|Agency|Studios?|Labs?|Tech|Technologies|Systems|Holdings|Capital|Ventures|Enterprises|Industries|Manufacturing)'
    
    branded_pattern = rf'^([A-Z][a-zA-Z]+(?:\s+[A-Z]?[a-zA-Z&\'-]+)*\s+{business_nouns})\s+{action_verbs}'
    match = re.match(branded_pattern, text)
    if match:
        candidate = match.group(1).strip()
        if _is_valid_branded_company(candidate):
            return candidate
    
    two_word_pattern = rf'^([A-Z][a-zA-Z]+\s+[A-Z][a-zA-Z]+)\s+{action_verbs}'
    match = re.match(two_word_pattern, text)
    if match:
        candidate = match.group(1).strip()
        if _is_valid_branded_company(candidate):
            return candidate
    
    return None


def _is_valid_branded_company(candidate: str) -> bool:
    """
    Validate that a candidate string looks like a real branded company name.
    
    ACCEPT: "Miami Best Roofing", "Cool Running Air", "Sunny Bliss Plumbing & Air"
    REJECT: "Texas HVAC company buys new", "Owner of Orlando", "Florida Vets", "South"
    """
    if not candidate or len(candidate) < 4 or len(candidate) > 60:
        return False
    
    words = candidate.split()
    if len(words) < 2 or len(words) > 6:
        return False
    
    poison_patterns = [
        r'^(?:texas|florida|miami|orlando|south|north|east|west|local|global|new|major|the|this|a|an)',
        r'^(?:owner|trump|billionaire|breaking|latest|update|how|why|what|when|after|before)',
        r'(?:company|business|firm|shop|store)\s+(?:buys?|sells?|opens?|files?|seeks?)',
        r'(?:vets?|veteran|supporter|worker|employee|customer)',
        r'^[a-z]',
    ]
    
    for pattern in poison_patterns:
        if re.search(pattern, candidate, re.IGNORECASE):
            return False
    
    business_indicators = [
        'roofing', 'air', 'plumbing', 'hvac', 'electric', 'landscaping', 'construction',
        'realty', 'properties', 'solutions', 'services', 'partners', 'group', 'corp',
        'inc', 'llc', 'company', 'associates', 'consulting', 'agency', 'studios',
        'labs', 'tech', 'technologies', 'systems', 'holdings', 'capital', 'ventures',
        'enterprises', 'industries', 'manufacturing', 'distribution', 'logistics'
    ]
    
    has_business_indicator = any(ind in candidate.lower() for ind in business_indicators)
    
    first_word = words[0]
    has_branded_start = first_word[0].isupper() and len(first_word) >= 3
    
    if has_business_indicator and has_branded_start:
        return True
    
    if len(words) >= 2 and has_branded_start:
        second_word = words[1]
        if second_word[0].isupper() and len(second_word) >= 3:
            if second_word.lower() not in ['the', 'and', 'of', 'for', 'in', 'on', 'at', 'to']:
                return True
    
    return False


def _tokenize_company_name(company_name: str) -> Set[str]:
    """
    Extract meaningful tokens from company name.
    
    Removes common suffixes (Inc, LLC, etc) and returns lowercase tokens.
    """
    if not company_name:
        return set()
    
    name = company_name.lower().strip()
    
    suffixes = [
        r'\s+(inc|llc|corp|co|ltd|llp|pllc|pc|pa|plc|lp|incorporated|corporation|company)\.?$',
        r'\s+&\s+', r'\s+and\s+',
    ]
    for suffix in suffixes:
        name = re.sub(suffix, ' ', name, flags=re.IGNORECASE)
    
    name = re.sub(r'[^a-z0-9\s]', ' ', name)
    
    tokens = set(name.split())
    
    stop_words = {'the', 'a', 'an', 'of', 'in', 'at', 'on', 'for', 'by', 'to', 'and', 'or'}
    tokens = tokens - stop_words
    
    tokens = {t for t in tokens if len(t) >= 2}
    
    return tokens


def _domain_matches_company(domain: str, company_name: str) -> Tuple[bool, float]:
    """
    Check if domain matches company name.
    
    Returns (is_match, confidence_score).
    """
    if not domain or not company_name:
        return False, 0.0
    
    domain_lower = domain.lower().replace("www.", "")
    domain_name = domain_lower.split(".")[0]
    
    company_tokens = _tokenize_company_name(company_name)
    
    if not company_tokens:
        return False, 0.0
    
    domain_slug = re.sub(r'[^a-z0-9]', '', domain_name)
    company_slug = "".join(sorted(company_tokens))
    
    matches = 0
    for token in company_tokens:
        if token in domain_name:
            matches += 1
    
    if matches == 0:
        return False, 0.0
    
    confidence = matches / len(company_tokens)
    
    combined_tokens = "".join(sorted(company_tokens))
    if domain_slug == combined_tokens or combined_tokens in domain_slug:
        confidence = 1.0
    
    return confidence >= 0.5, confidence


def _fetch_page(url: str, timeout: Optional[int] = None) -> Optional[str]:
    """
    Fetch a page with retry and error handling.
    
    Returns HTML content or None if failed.
    """
    if timeout is None:
        timeout = DISCOVERY_TIMEOUT
    
    try:
        headers = {
            "User-Agent": _get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        
        if response.status_code == 200:
            return response.text
        
        return None
        
    except (RequestException, Timeout) as e:
        log_discovery("fetch_error", details={"url": url[:50]}, error=str(e)[:100])
        return None


def _extract_outbound_links(html: str, base_url: str) -> List[str]:
    """
    Extract outbound links from HTML that might be company websites.
    
    Filters out social/news/directory links.
    """
    if not html:
        return []
    
    link_pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', re.IGNORECASE)
    
    matches = link_pattern.findall(html)
    
    base_domain = _normalize_domain(base_url)
    
    candidate_domains = []
    
    for href, anchor_text in matches:
        if not href.startswith("http"):
            continue
        
        domain = _normalize_domain(href)
        
        if not domain:
            continue
        
        if domain == base_domain:
            continue
        
        if _is_blocked_domain(domain):
            continue
        
        if not _has_valid_tld(domain):
            continue
        
        candidate_domains.append(domain)
    
    return list(set(candidate_domains))


def _guess_domain_from_company_name(company_name: str) -> Optional[str]:
    """
    Attempt to guess domain from company name.
    
    Example: "Cool Running Air" -> "coolrunningair.com"
    """
    if not company_name:
        return None
    
    tokens = _tokenize_company_name(company_name)
    
    if not tokens:
        return None
    
    slug = "".join(sorted(tokens, key=lambda x: company_name.lower().find(x)))
    
    if len(slug) < 3:
        return None
    
    return f"{slug}.com"


def discover_domain_from_existing_fields(
    lead_domain: Optional[str],
    lead_email: Optional[str],
    lead_company: Optional[str]
) -> DomainDiscoveryResult:
    """
    Layer 1: Extract domain from existing LeadEvent fields.
    
    Priority:
    1. lead_domain (if valid and not blocked)
    2. lead_email (extract domain part)
    """
    
    if lead_domain:
        domain = _normalize_domain(lead_domain)
        if domain and not _is_blocked_domain(domain) and _has_valid_tld(domain):
            match, confidence = _domain_matches_company(domain, lead_company) if lead_company else (True, 0.8)
            return DomainDiscoveryResult(
                success=True,
                domain=domain,
                source="existing_field",
                confidence=confidence if confidence > 0 else 0.8,
                company_name_match=match,
                discovery_method="lead_domain",
                attempts=1
            )
    
    if lead_email and "@" in lead_email:
        email_domain = lead_email.split("@")[1].lower()
        if not _is_blocked_domain(email_domain) and _has_valid_tld(email_domain):
            match, confidence = _domain_matches_company(email_domain, lead_company) if lead_company else (True, 0.9)
            return DomainDiscoveryResult(
                success=True,
                domain=email_domain,
                source="existing_field",
                confidence=confidence if confidence > 0 else 0.9,
                company_name_match=match,
                discovery_method="lead_email",
                attempts=1
            )
    
    return DomainDiscoveryResult(
        success=False,
        source="existing_field",
        discovery_method="none",
        attempts=1,
        error="No usable domain in existing fields"
    )


def discover_domain_from_source_url(
    source_url: Optional[str],
    company_name: Optional[str]
) -> DomainDiscoveryResult:
    """
    Layer 2: Extract domain from signal source URL.
    
    If source_url is a company website (not news/directory), use it directly.
    If source_url is a news article, fetch and parse for outbound links.
    """
    if not source_url:
        return DomainDiscoveryResult(
            success=False,
            source="source_url",
            discovery_method="none",
            attempts=0,
            error="No source URL provided"
        )
    
    source_domain = _normalize_domain(source_url)
    
    if not source_domain:
        return DomainDiscoveryResult(
            success=False,
            source="source_url",
            discovery_method="none",
            attempts=1,
            error="Could not parse source URL"
        )
    
    if not _is_blocked_domain(source_domain):
        match, confidence = _domain_matches_company(source_domain, company_name) if company_name else (False, 0.5)
        
        if match or confidence >= 0.5:
            return DomainDiscoveryResult(
                success=True,
                domain=source_domain,
                source="source_url",
                confidence=confidence,
                company_name_match=match,
                discovery_method="source_url_direct",
                attempts=1
            )
    
    log_discovery("fetch_article", details={"url": source_url[:50], "reason": "news_site"})
    
    time.sleep(random.uniform(DISCOVERY_DELAY_MIN, DISCOVERY_DELAY_MAX))
    
    html = _fetch_page(source_url)
    
    if not html:
        return DomainDiscoveryResult(
            success=False,
            source="source_url",
            discovery_method="article_parse",
            attempts=1,
            error="Could not fetch article page"
        )
    
    candidate_domains = _extract_outbound_links(html, source_url)
    
    if not candidate_domains:
        return DomainDiscoveryResult(
            success=False,
            source="source_url",
            discovery_method="article_parse",
            attempts=1,
            error="No candidate domains found in article"
        )
    
    if company_name:
        best_match = None
        best_confidence = 0.0
        
        for domain in candidate_domains:
            match, confidence = _domain_matches_company(domain, company_name)
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = domain
        
        if best_match and best_confidence >= 0.5:
            return DomainDiscoveryResult(
                success=True,
                domain=best_match,
                source="source_url",
                confidence=best_confidence,
                company_name_match=True,
                discovery_method="article_link_match",
                attempts=1
            )
    
    if len(candidate_domains) == 1:
        return DomainDiscoveryResult(
            success=True,
            domain=candidate_domains[0],
            source="source_url",
            confidence=0.6,
            company_name_match=False,
            discovery_method="article_single_link",
            attempts=1
        )
    
    return DomainDiscoveryResult(
        success=False,
        source="source_url",
        discovery_method="article_parse",
        attempts=1,
        error=f"Multiple candidates ({len(candidate_domains)}), no clear match"
    )


_ddg_consecutive_failures = 0
_ddg_last_failure_time = 0.0

def _search_duckduckgo_html(query: str) -> List[str]:
    """
    DOMAINSTORM: Search DuckDuckGo HTML for domains.
    
    Fetches DuckDuckGo search results as HTML and extracts result domains.
    No API key required - pure HTML scraping.
    Includes backoff logic to avoid throttling.
    """
    global _ddg_consecutive_failures, _ddg_last_failure_time
    
    if _ddg_consecutive_failures >= 3:
        time_since_failure = time.time() - _ddg_last_failure_time
        if time_since_failure < 300:
            log_discovery("ddg_backoff", details={"failures": _ddg_consecutive_failures, "wait_remaining": int(300 - time_since_failure)})
            return []
        else:
            _ddg_consecutive_failures = 0
    
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": _get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        data = {"q": query, "b": ""}
        
        delay = random.uniform(DISCOVERY_DELAY_MIN, DISCOVERY_DELAY_MAX) * (1 + _ddg_consecutive_failures * 0.5)
        time.sleep(delay)
        
        start_time = time.time()
        response = requests.post(url, headers=headers, data=data, timeout=DISCOVERY_TIMEOUT)
        fetch_time = time.time() - start_time
        
        if response.status_code != 200:
            _ddg_consecutive_failures += 1
            _ddg_last_failure_time = time.time()
            log_discovery("ddg_http_error", details={"status": response.status_code, "query": query[:40]})
            return []
        
        if "blocked" in response.text.lower() or "captcha" in response.text.lower():
            _ddg_consecutive_failures += 1
            _ddg_last_failure_time = time.time()
            log_discovery("ddg_blocked", details={"query": query[:40]})
            return []
        
        html = response.text
        
        domains = []
        result_pattern = re.compile(r'href="//duckduckgo\.com/l/\?uddg=([^"&]+)"', re.IGNORECASE)
        
        for match in result_pattern.finditer(html):
            try:
                from urllib.parse import unquote
                result_url = unquote(match.group(1))
                domain = _normalize_domain(result_url)
                
                if domain and not _is_blocked_domain(domain) and _has_valid_tld(domain):
                    if domain not in domains:
                        domains.append(domain)
            except Exception:
                continue
        
        if not domains:
            direct_pattern = re.compile(r'class="result__url"[^>]*>([^<]+)<', re.IGNORECASE)
            for match in direct_pattern.finditer(html):
                domain_text = match.group(1).strip()
                domain = _normalize_domain(domain_text)
                if domain and not _is_blocked_domain(domain) and _has_valid_tld(domain):
                    if domain not in domains:
                        domains.append(domain)
        
        _ddg_consecutive_failures = 0
        
        if domains:
            log_discovery("ddg_success", details={"domains": len(domains), "fetch_time": f"{fetch_time:.2f}s"})
        
        return domains[:5]
    
    except requests.Timeout:
        _ddg_consecutive_failures += 1
        _ddg_last_failure_time = time.time()
        log_discovery("ddg_timeout", details={"query": query[:40]})
        return []
    except requests.RequestException as e:
        _ddg_consecutive_failures += 1
        _ddg_last_failure_time = time.time()
        log_discovery("ddg_request_error", error=str(e)[:50])
        return []
    except Exception as e:
        _ddg_consecutive_failures += 1
        _ddg_last_failure_time = time.time()
        log_discovery("ddg_unexpected_error", error=str(e)[:50])
        return []


def discover_domain_via_web_search(
    company_name: str,
    geography: Optional[str] = None,
    niche: Optional[str] = None
) -> DomainDiscoveryResult:
    """
    Layer 3: Web search fallback using company name + geography + niche.
    
    DOMAINSTORM Enhanced:
    1. Try guessing domain from company name
    2. If guess fails, search DuckDuckGo HTML
    3. Match results against company name tokens
    """
    if not company_name:
        return DomainDiscoveryResult(
            success=False,
            source="web_search",
            discovery_method="none",
            attempts=0,
            error="No company name provided"
        )
    
    query_parts = [f'"{company_name}"']
    if geography:
        first_geo = geography.split(",")[0].strip()
        query_parts.append(first_geo)
    if niche:
        first_niche = niche.split(",")[0].strip()
        query_parts.append(first_niche)
    
    query = " ".join(query_parts)
    
    log_discovery("web_search", details={"query": query})
    
    guessed = _guess_domain_from_company_name(company_name)
    if guessed:
        try:
            time.sleep(random.uniform(DISCOVERY_DELAY_MIN, DISCOVERY_DELAY_MAX))
            
            test_url = f"https://{guessed}"
            headers = {"User-Agent": _get_random_user_agent()}
            
            response = requests.head(test_url, headers=headers, timeout=5, allow_redirects=True)
            
            if response.status_code < 400:
                html = _fetch_page(test_url)
                
                if html and company_name.lower() in html.lower():
                    return DomainDiscoveryResult(
                        success=True,
                        domain=guessed,
                        source="web_search",
                        confidence=0.85,
                        company_name_match=True,
                        discovery_method="guessed_domain_verified",
                        attempts=1
                    )
                
                return DomainDiscoveryResult(
                    success=True,
                    domain=guessed,
                    source="web_search",
                    confidence=0.6,
                    company_name_match=False,
                    discovery_method="guessed_domain_exists",
                    attempts=1
                )
        except Exception as e:
            log_discovery("guess_failed", details={"guessed": guessed}, error=str(e)[:50])
    
    log_discovery("ddg_search", details={"query": query})
    search_domains = _search_duckduckgo_html(query)
    
    if search_domains:
        best_domain = None
        best_confidence = 0.0
        
        for domain in search_domains:
            match, confidence = _domain_matches_company(domain, company_name)
            if confidence > best_confidence:
                best_domain = domain
                best_confidence = confidence
        
        if best_domain and best_confidence >= 0.5:
            log_discovery("ddg_match", details={"domain": best_domain, "confidence": best_confidence})
            return DomainDiscoveryResult(
                success=True,
                domain=best_domain,
                source="web_search",
                confidence=best_confidence,
                company_name_match=True,
                discovery_method="duckduckgo_search",
                attempts=2
            )
        
        if search_domains:
            first_domain = search_domains[0]
            log_discovery("ddg_first_result", details={"domain": first_domain})
            return DomainDiscoveryResult(
                success=True,
                domain=first_domain,
                source="web_search",
                confidence=0.5,
                company_name_match=False,
                discovery_method="duckduckgo_first_result",
                attempts=2
            )
    
    return DomainDiscoveryResult(
        success=False,
        source="web_search",
        discovery_method="exhausted",
        attempts=2,
        error="Domain guess and search both failed"
    )


def discover_domain_for_lead_event(
    lead_event_id: int,
    lead_domain: Optional[str] = None,
    lead_email: Optional[str] = None,
    lead_company: Optional[str] = None,
    source_url: Optional[str] = None,
    geography: Optional[str] = None,
    niche: Optional[str] = None,
    summary: Optional[str] = None
) -> DomainDiscoveryResult:
    """
    Main entry point: Layered domain discovery for a LeadEvent.
    
    Executes discovery layers in order:
    1. Extract from existing fields (lead_domain, lead_email)
    2. Extract from source_url (company site or article parsing)
    3. Web search fallback (company name + geography + niche)
    
    Returns the first successful result with confidence > threshold.
    """
    log_discovery("start", lead_event_id=lead_event_id, 
                  details={"company": lead_company, "has_domain": bool(lead_domain), 
                           "has_email": bool(lead_email), "has_source_url": bool(source_url)})
    
    total_attempts = 0
    
    result1 = discover_domain_from_existing_fields(lead_domain, lead_email, lead_company)
    total_attempts += result1.attempts
    
    if result1.success:
        log_discovery("success", lead_event_id=lead_event_id, domain=result1.domain,
                      details={"method": result1.discovery_method, "confidence": result1.confidence})
        result1.attempts = total_attempts
        return result1
    
    effective_source_url = source_url
    if not effective_source_url and summary:
        url_match = re.search(r'https?://[^\s<>"]+', summary)
        if url_match:
            effective_source_url = url_match.group(0)
    
    if effective_source_url:
        result2 = discover_domain_from_source_url(effective_source_url, lead_company)
        total_attempts += result2.attempts
        
        if result2.success:
            log_discovery("success", lead_event_id=lead_event_id, domain=result2.domain,
                          details={"method": result2.discovery_method, "confidence": result2.confidence})
            result2.attempts = total_attempts
            return result2
    
    if lead_company:
        result3 = discover_domain_via_web_search(lead_company, geography, niche)
        total_attempts += result3.attempts
        
        if result3.success:
            log_discovery("success", lead_event_id=lead_event_id, domain=result3.domain,
                          details={"method": result3.discovery_method, "confidence": result3.confidence})
            result3.attempts = total_attempts
            return result3
    
    log_discovery("failed", lead_event_id=lead_event_id,
                  details={"attempts": total_attempts, "company": lead_company})
    
    return DomainDiscoveryResult(
        success=False,
        source="all_layers",
        discovery_method="exhausted",
        attempts=total_attempts,
        error="All discovery layers failed"
    )


print("[DOMAIN_DISCOVERY][STARTUP] Module loaded - aggressive domain hunting enabled")
