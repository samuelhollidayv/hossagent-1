"""
OPERATION PHONESTORM: Phone Number Extraction and Enrichment Pipeline

Extracts, validates, normalizes, and scores phone numbers from web pages
to accelerate domain discovery and provide additional contact channels.

Phone numbers serve as:
- Identity anchors
- Domain-discovery accelerators
- Contact-channel fallbacks
- Enrichment multipliers

Pure web scraping - NO paid APIs required.
"""

import os
import re
import time
import random
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Set
from urllib.parse import urljoin, urlparse
import requests
from requests.exceptions import RequestException, Timeout


PHONE_TIMEOUT = int(os.getenv("PHONE_EXTRACTION_TIMEOUT", "10"))
PHONE_MAX_PAGES = int(os.getenv("PHONE_EXTRACTION_MAX_PAGES", "5"))
PHONE_DELAY_MIN = float(os.getenv("PHONE_EXTRACTION_DELAY_MIN", "1.0"))
PHONE_DELAY_MAX = float(os.getenv("PHONE_EXTRACTION_DELAY_MAX", "3.0"))


PHONE_REGEX_PATTERNS = [
    re.compile(r'\+?1[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'),
    re.compile(r'\(\d{3}\)\s*\d{3}[-.\s]?\d{4}'),
    re.compile(r'\d{3}[-.\s]\d{3}[-.\s]\d{4}'),
    re.compile(r'\d{10}'),
]

TEL_LINK_REGEX = re.compile(r'href=["\']tel:([^"\']+)["\']', re.IGNORECASE)

SCHEMA_PHONE_REGEX = re.compile(r'"telephone"\s*:\s*"([^"]+)"', re.IGNORECASE)

TOLL_FREE_PREFIXES = ['800', '888', '877', '866', '855', '844', '833']

MOBILE_PREFIXES = [
    '201', '202', '203', '205', '206', '207', '208', '209', '210', '212',
    '213', '214', '215', '216', '217', '218', '219', '224', '225', '228',
    '229', '231', '234', '239', '240', '248', '251', '252', '253', '254',
    '256', '260', '262', '267', '269', '270', '272', '276', '281', '301',
    '302', '303', '304', '305', '307', '308', '309', '310', '312', '313',
    '314', '315', '316', '317', '318', '319', '320', '321', '323', '325',
    '330', '331', '334', '336', '337', '339', '346', '347', '351', '352',
    '360', '361', '364', '380', '385', '386', '401', '402', '404', '405',
    '406', '407', '408', '409', '410', '412', '413', '414', '415', '417',
    '419', '423', '424', '425', '430', '432', '434', '435', '440', '442',
    '443', '458', '469', '470', '475', '478', '479', '480', '484', '501',
    '502', '503', '504', '505', '507', '508', '509', '510', '512', '513',
    '515', '516', '517', '518', '520', '530', '531', '534', '539', '540',
    '541', '551', '559', '561', '562', '563', '564', '567', '570', '571',
    '573', '574', '575', '580', '585', '586', '601', '602', '603', '605',
    '606', '607', '608', '609', '610', '612', '614', '615', '616', '617',
    '618', '619', '620', '623', '626', '628', '629', '630', '631', '636',
    '641', '646', '650', '651', '657', '660', '661', '662', '667', '669',
    '678', '681', '682', '689', '701', '702', '703', '704', '706', '707',
    '708', '712', '713', '714', '715', '716', '717', '718', '719', '720',
    '724', '725', '727', '731', '732', '734', '737', '740', '743', '747',
    '754', '757', '760', '762', '763', '765', '769', '770', '772', '773',
    '774', '775', '779', '781', '785', '786', '801', '802', '803', '804',
    '805', '806', '808', '810', '812', '813', '814', '815', '816', '817',
    '818', '828', '830', '831', '832', '843', '845', '847', '848', '850',
    '856', '857', '858', '859', '860', '862', '863', '864', '865', '870',
    '878', '901', '903', '904', '906', '907', '908', '909', '910', '912',
    '913', '914', '915', '916', '917', '918', '919', '920', '925', '928',
    '929', '930', '931', '934', '936', '937', '938', '940', '941', '947',
    '949', '951', '952', '954', '956', '959', '970', '971', '972', '973',
    '978', '979', '980', '984', '985', '989'
]

SOUTH_FLORIDA_AREA_CODES = ['305', '786', '954', '754', '561', '772']

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

_seen_phones: Dict[str, Set[str]] = {}
_phone_cache: Dict[str, Tuple['PhoneDiscoveryResult', datetime]] = {}
CACHE_TTL_HOURS = 24


@dataclass
class DiscoveredPhone:
    """Represents a discovered phone number with metadata."""
    raw_number: str
    e164_number: str
    confidence: float
    source: str
    phone_type: str
    source_url: str
    discovered_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict:
        return {
            "raw_number": self.raw_number,
            "e164_number": self.e164_number,
            "confidence": self.confidence,
            "source": self.source,
            "phone_type": self.phone_type,
            "source_url": self.source_url,
            "discovered_at": self.discovered_at.isoformat()
        }


@dataclass
class PhoneDiscoveryResult:
    """Result of phone discovery attempt."""
    success: bool
    domain: str
    phones: List[DiscoveredPhone] = field(default_factory=list)
    best_phone: Optional[DiscoveredPhone] = None
    pages_checked: int = 0
    error: Optional[str] = None
    duration_ms: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "domain": self.domain,
            "phones": [p.to_dict() for p in self.phones],
            "best_phone": self.best_phone.to_dict() if self.best_phone else None,
            "pages_checked": self.pages_checked,
            "error": self.error,
            "duration_ms": self.duration_ms
        }


def _normalize_to_e164(phone: str) -> Optional[str]:
    """
    Normalize phone number to E.164 format: +1XXXYYYZZZZ
    Returns None if normalization fails.
    """
    digits = re.sub(r'\D', '', phone)
    
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith('1'):
        return f"+{digits}"
    elif len(digits) == 12 and digits.startswith('01'):
        return f"+{digits[1:]}"
    else:
        return None


def _extract_digits(phone: str) -> str:
    """Extract only digits from phone string."""
    return re.sub(r'\D', '', phone)


def _get_area_code(e164: str) -> Optional[str]:
    """Extract area code from E.164 number."""
    if e164 and len(e164) >= 5:
        return e164[2:5]
    return None


def _classify_phone_type(e164: str) -> str:
    """
    Classify phone type based on area code patterns.
    Returns: mobile, landline, voip, tollfree, unknown
    """
    if not e164:
        return "unknown"
    
    area_code = _get_area_code(e164)
    if not area_code:
        return "unknown"
    
    if area_code in TOLL_FREE_PREFIXES:
        return "tollfree"
    
    if area_code in MOBILE_PREFIXES:
        return "mobile"
    
    return "landline"


def _is_valid_us_phone(e164: str) -> bool:
    """Check if E.164 number is a valid US phone with spam filtering."""
    if not e164 or not e164.startswith('+1'):
        return False
    
    digits = _extract_digits(e164)
    if len(digits) != 11:
        return False
    
    area_code = digits[1:4]
    if area_code[0] in ['0', '1']:
        return False
    
    exchange = digits[4:7]
    if exchange[0] in ['0', '1']:
        return False
    
    subscriber = digits[1:]  # 10 digits after +1
    
    if len(set(subscriber)) <= 2:
        return False
    
    repeated_patterns = ['1234567890', '0123456789', '9876543210']
    if subscriber in repeated_patterns:
        return False
    
    if subscriber == subscriber[0] * 10:
        return False
    
    easy_patterns = ['5555555555', '0000000000', '1111111111']
    if subscriber in easy_patterns:
        return False
    
    if area_code in TOLL_FREE_PREFIXES:
        return False
    
    return True


def _calculate_phone_confidence(
    phone: DiscoveredPhone,
    page_type: str,
    is_local: bool = False
) -> float:
    """
    Calculate confidence score for discovered phone.
    
    Scoring factors:
    - Schema.org telephone: +0.9
    - Tel: link: +0.8
    - Contact page: +0.8
    - Homepage: +0.7
    - Footer: +0.6
    - Random page: +0.3
    
    Penalties:
    - Toll-free: -0.5
    - Seen on multiple domains: -0.3
    
    Bonuses:
    - South Florida area code: +0.1
    - Mobile type: +0.1
    """
    score = 0.0
    
    if phone.source == "schema":
        score = 0.9
    elif phone.source == "tel_link":
        score = 0.8
    elif page_type == "contact":
        score = 0.8
    elif page_type == "homepage":
        score = 0.7
    elif page_type == "footer":
        score = 0.6
    else:
        score = 0.3
    
    if phone.phone_type == "tollfree":
        score -= 0.5
    
    area_code = _get_area_code(phone.e164_number)
    if area_code and phone.e164_number in _seen_phones:
        if len(_seen_phones[phone.e164_number]) > 4:
            score -= 0.3
    
    if area_code in SOUTH_FLORIDA_AREA_CODES:
        score += 0.1
    
    if phone.phone_type == "mobile":
        score += 0.1
    
    return max(0.0, min(1.0, score))


def _get_random_user_agent() -> str:
    """Get random user agent."""
    return random.choice(USER_AGENTS)


def _polite_delay() -> None:
    """Apply polite delay between requests."""
    delay = random.uniform(PHONE_DELAY_MIN, PHONE_DELAY_MAX)
    time.sleep(delay)


def _fetch_page(url: str) -> Optional[str]:
    """Fetch page content with error handling."""
    try:
        headers = {
            "User-Agent": _get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        
        response = requests.get(
            url,
            headers=headers,
            timeout=PHONE_TIMEOUT,
            allow_redirects=True,
            verify=True
        )
        
        if response.status_code == 200:
            return response.text
        else:
            print(f"[PHONESTORM] HTTP {response.status_code} for {url}")
            return None
            
    except Timeout:
        print(f"[PHONESTORM] Timeout fetching {url}")
        return None
    except RequestException as e:
        print(f"[PHONESTORM] Request error for {url}: {str(e)[:100]}")
        return None


def _extract_phones_from_html(
    html: str,
    source_url: str,
    page_type: str = "other"
) -> List[DiscoveredPhone]:
    """
    Extract phone numbers from HTML using multiple methods.
    
    Methods:
    1. tel: link extraction
    2. Schema.org telephone property
    3. Regex patterns in body text
    4. Footer region extraction
    """
    phones = []
    seen_e164 = set()
    
    tel_matches = TEL_LINK_REGEX.findall(html)
    for match in tel_matches:
        e164 = _normalize_to_e164(match)
        if e164 and _is_valid_us_phone(e164) and e164 not in seen_e164:
            seen_e164.add(e164)
            phone_type = _classify_phone_type(e164)
            phone = DiscoveredPhone(
                raw_number=match,
                e164_number=e164,
                confidence=0.0,
                source="tel_link",
                phone_type=phone_type,
                source_url=source_url
            )
            phone.confidence = _calculate_phone_confidence(phone, page_type)
            phones.append(phone)
            print(f"[PHONESTORM][FOUND_PHONE] number={e164}, conf={phone.confidence:.2f}, source=tel_link")
    
    schema_matches = SCHEMA_PHONE_REGEX.findall(html)
    for match in schema_matches:
        e164 = _normalize_to_e164(match)
        if e164 and _is_valid_us_phone(e164) and e164 not in seen_e164:
            seen_e164.add(e164)
            phone_type = _classify_phone_type(e164)
            phone = DiscoveredPhone(
                raw_number=match,
                e164_number=e164,
                confidence=0.0,
                source="schema",
                phone_type=phone_type,
                source_url=source_url
            )
            phone.confidence = _calculate_phone_confidence(phone, page_type)
            phones.append(phone)
            print(f"[PHONESTORM][FOUND_PHONE] number={e164}, conf={phone.confidence:.2f}, source=schema")
    
    footer_match = re.search(r'<footer[^>]*>(.*?)</footer>', html, re.IGNORECASE | re.DOTALL)
    if footer_match:
        footer_html = footer_match.group(1)
        for pattern in PHONE_REGEX_PATTERNS:
            matches = pattern.findall(footer_html)
            for match in matches:
                e164 = _normalize_to_e164(match)
                if e164 and _is_valid_us_phone(e164) and e164 not in seen_e164:
                    seen_e164.add(e164)
                    phone_type = _classify_phone_type(e164)
                    phone = DiscoveredPhone(
                        raw_number=match,
                        e164_number=e164,
                        confidence=0.0,
                        source="footer",
                        phone_type=phone_type,
                        source_url=source_url
                    )
                    phone.confidence = _calculate_phone_confidence(phone, "footer")
                    phones.append(phone)
                    print(f"[PHONESTORM][FOUND_PHONE] number={e164}, conf={phone.confidence:.2f}, source=footer")
    
    for pattern in PHONE_REGEX_PATTERNS:
        matches = pattern.findall(html)
        for match in matches:
            e164 = _normalize_to_e164(match)
            if e164 and _is_valid_us_phone(e164) and e164 not in seen_e164:
                seen_e164.add(e164)
                phone_type = _classify_phone_type(e164)
                phone = DiscoveredPhone(
                    raw_number=match,
                    e164_number=e164,
                    confidence=0.0,
                    source=page_type,
                    phone_type=phone_type,
                    source_url=source_url
                )
                phone.confidence = _calculate_phone_confidence(phone, page_type)
                phones.append(phone)
                print(f"[PHONESTORM][FOUND_PHONE] number={e164}, conf={phone.confidence:.2f}, source={page_type}")
    
    return phones


def _determine_page_type(url: str) -> str:
    """Determine page type from URL path."""
    path = urlparse(url).path.lower()
    
    if path in ['/', '']:
        return "homepage"
    elif 'contact' in path:
        return "contact"
    elif 'about' in path:
        return "about"
    elif 'team' in path or 'staff' in path:
        return "team"
    else:
        return "other"


def _get_cached_result(domain: str) -> Optional[PhoneDiscoveryResult]:
    """Check cache for recent discovery result."""
    if domain in _phone_cache:
        result, cached_at = _phone_cache[domain]
        if datetime.utcnow() - cached_at < timedelta(hours=CACHE_TTL_HOURS):
            return result
        else:
            del _phone_cache[domain]
    return None


def _cache_result(domain: str, result: PhoneDiscoveryResult) -> None:
    """Cache discovery result."""
    _phone_cache[domain] = (result, datetime.utcnow())


def _track_phone_domain(e164: str, domain: str) -> None:
    """Track which domains a phone number has been seen on."""
    if e164 not in _seen_phones:
        _seen_phones[e164] = set()
    _seen_phones[e164].add(domain)


def discover_phones(domain: str) -> PhoneDiscoveryResult:
    """
    Discover phone numbers from a company website.
    
    Args:
        domain: Company domain (e.g., "example.com")
        
    Returns:
        PhoneDiscoveryResult with found phones and metadata
    """
    start_time = time.time()
    domain = domain.lower().strip().lstrip("www.")
    
    cached = _get_cached_result(domain)
    if cached:
        print(f"[PHONESTORM] Cache hit for {domain}")
        return cached
    
    print(f"[PHONESTORM] Starting phone discovery for {domain}")
    
    all_phones: List[DiscoveredPhone] = []
    pages_checked = 0
    
    base_url = f"https://{domain}"
    homepage_html = _fetch_page(base_url)
    
    if not homepage_html:
        base_url = f"https://www.{domain}"
        homepage_html = _fetch_page(base_url)
    
    if homepage_html:
        pages_checked += 1
        phones = _extract_phones_from_html(homepage_html, base_url, "homepage")
        all_phones.extend(phones)
        
        contact_paths = ['/contact', '/contact-us', '/about', '/about-us']
        for path in contact_paths:
            if pages_checked >= PHONE_MAX_PAGES:
                break
            
            _polite_delay()
            url = urljoin(base_url, path)
            page_html = _fetch_page(url)
            
            if page_html:
                pages_checked += 1
                page_type = _determine_page_type(url)
                phones = _extract_phones_from_html(page_html, url, page_type)
                
                for phone in phones:
                    if not any(p.e164_number == phone.e164_number for p in all_phones):
                        all_phones.append(phone)
    
    for phone in all_phones:
        _track_phone_domain(phone.e164_number, domain)
    
    all_phones.sort(key=lambda p: -p.confidence)
    
    best_phone = None
    if all_phones:
        non_tollfree = [p for p in all_phones if p.phone_type != "tollfree"]
        if non_tollfree:
            best_phone = non_tollfree[0]
        else:
            best_phone = all_phones[0]
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    result = PhoneDiscoveryResult(
        success=len(all_phones) > 0,
        domain=domain,
        phones=all_phones,
        best_phone=best_phone,
        pages_checked=pages_checked,
        duration_ms=duration_ms
    )
    
    _cache_result(domain, result)
    
    if result.success:
        print(f"[PHONESTORM] Found {len(all_phones)} phone(s) for {domain}, best: {best_phone.e164_number if best_phone else 'none'}")
        print(f"[PHONESTORM][PHONE_TYPE] {best_phone.phone_type if best_phone else 'unknown'}")
        print(f"[PHONESTORM][PHONE_CONFIDENCE] score={best_phone.confidence if best_phone else 0.0:.2f}")
    else:
        print(f"[PHONESTORM] No phones found for {domain} (checked {pages_checked} pages)")
    
    return result


def get_domain_from_phone(phone: str, business_name: Optional[str] = None, city: Optional[str] = None) -> Optional[str]:
    """
    Attempt to discover domain from phone number using reverse lookup.
    
    This is a fallback when ARCHANGEL cannot parse website URLs.
    Uses internal cache first, then web search patterns.
    
    Args:
        phone: Phone number (any format)
        business_name: Optional business name for search refinement
        city: Optional city for geographic filtering
        
    Returns:
        Domain string if found, None otherwise
    """
    e164 = _normalize_to_e164(phone)
    if not e164:
        return None
    
    if e164 in _seen_phones and _seen_phones[e164]:
        domains = list(_seen_phones[e164])
        if len(domains) == 1:
            print(f"[PHONESTORM][REVERSE_DOMAIN] Found cached domain for {e164}: {domains[0]}")
            return domains[0]
        else:
            print(f"[PHONESTORM][REVERSE_DOMAIN] Multiple domains for {e164}, cannot determine unique match")
    
    print(f"[PHONESTORM][REVERSE_DOMAIN] No cached domain for {e164}")
    return None


def generate_sms_suggestion(lead_name: str, signal_context: str) -> str:
    """
    Generate suggested SMS message for manual follow-up.
    
    Args:
        lead_name: First name of lead
        signal_context: Brief context from signal (e.g., "your Miami expansion")
        
    Returns:
        Suggested SMS text
    """
    first_name = lead_name.split()[0] if lead_name else "there"
    
    return f"Hey {first_name}, Sam Holliday here in Miami — saw {signal_context}. Wanted to share a quick local insight; is this the right number?"


def generate_call_script(lead_name: str, lead_company: str, signal_context: str) -> str:
    """
    Generate suggested call script for manual follow-up.
    
    Args:
        lead_name: Full name of lead
        lead_company: Company name
        signal_context: Context from signal
        
    Returns:
        Call script text
    """
    first_name = lead_name.split()[0] if lead_name else "there"
    
    return f"""Hi {first_name}, this is Sam Holliday calling from Miami.

I noticed {lead_company} {signal_context} and wanted to reach out because I work with a lot of businesses in South Florida facing similar situations.

I've put together some insights specifically for companies making this kind of move that I thought might be valuable.

Do you have a couple minutes to chat, or would it be better if I sent you something to review first?"""


def get_phone_discovery_status() -> Dict:
    """Get current phone discovery configuration status."""
    return {
        "timeout_seconds": PHONE_TIMEOUT,
        "max_pages_per_domain": PHONE_MAX_PAGES,
        "delay_range": f"{PHONE_DELAY_MIN}-{PHONE_DELAY_MAX}s",
        "cache_size": len(_phone_cache),
        "tracked_phones": len(_seen_phones),
        "cache_ttl_hours": CACHE_TTL_HOURS
    }


if __name__ == "__main__":
    import json
    
    test_domains = ["hossagent.net"]
    
    print("PHONESTORM Discovery Test")
    print("=" * 50)
    print(f"Config: {get_phone_discovery_status()}")
    print("=" * 50)
    
    for domain in test_domains:
        print(f"\nTesting: {domain}")
        result = discover_phones(domain)
        print(f"Result: {json.dumps(result.to_dict(), indent=2)}")
