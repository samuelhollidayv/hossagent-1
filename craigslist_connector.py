"""
OPERATION SIGNALSTORM v1: Craigslist Connector

Scrapes Craigslist for SMB-heavy signals:
- Services offered (hvac, plumbing, roofing, landscaping)
- For sale > business (business sales)
- Jobs > trades (hiring signals)
- Housing > office/commercial (expansion signals)

Focuses on South Florida markets: Miami, Fort Lauderdale, Palm Beach.

Pure web scraping - NO paid APIs.
"""

import os
import re
import json
import time
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Generator
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlencode, quote

import requests
from requests.exceptions import RequestException, Timeout


CRAIGSLIST_TIMEOUT = int(os.getenv("CRAIGSLIST_TIMEOUT", "10"))
CRAIGSLIST_RATE_LIMIT = float(os.getenv("CRAIGSLIST_RATE_LIMIT", "2.0"))
CRAIGSLIST_DRY_RUN = os.getenv("CRAIGSLIST_DRY_RUN", "false").lower() in ("true", "1", "yes")

SOUTH_FLORIDA_REGIONS = {
    "miami": "https://miami.craigslist.org",
    "fortlauderdale": "https://fortlauderdale.craigslist.org",
    "palmbeach": "https://palmbeach.craigslist.org",
}

SERVICE_CATEGORIES = {
    "hvac": "/search/sss?query=hvac",
    "plumbing": "/search/sss?query=plumber",
    "roofing": "/search/sss?query=roofing",
    "electrical": "/search/sss?query=electrician",
    "landscaping": "/search/sss?query=landscaping",
    "pool": "/search/sss?query=pool+service",
    "cleaning": "/search/sss?query=cleaning+service",
    "painting": "/search/sss?query=painting+contractor",
}

SIGNAL_CATEGORIES = {
    "business_for_sale": "/search/bfs",
    "services_offered": "/search/bbb",
    "gigs_labor": "/search/lbg",
    "trades_jobs": "/search/trd",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

NICHE_KEYWORDS = {
    "hvac": ["hvac", "air conditioning", "ac repair", "ac service", "heating", "cooling", "duct"],
    "plumbing": ["plumber", "plumbing", "drain", "pipe", "water heater", "leak"],
    "roofing": ["roofing", "roof repair", "shingle", "roofer", "roof"],
    "electrical": ["electrician", "electrical", "wiring", "panel", "outlet"],
    "landscaping": ["landscaping", "lawn", "tree", "garden", "irrigation"],
    "pool": ["pool", "spa", "hot tub", "pool service", "pool cleaning"],
    "cleaning": ["cleaning", "maid", "janitorial", "housekeeping"],
    "painting": ["painting", "painter", "drywall", "interior painting", "exterior painting"],
    "moving": ["moving", "movers", "hauling", "junk removal"],
    "pest": ["pest control", "exterminator", "termite", "bug"],
    "auto": ["auto repair", "mechanic", "body shop", "collision"],
    "construction": ["construction", "contractor", "remodeling", "renovation", "builder"],
}


@dataclass
class CraigslistListing:
    """A Craigslist listing."""
    listing_id: str
    title: str
    url: str
    price: Optional[str] = None
    location: Optional[str] = None
    neighborhood: Optional[str] = None
    posted_date: Optional[str] = None
    body_text: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    images: List[str] = field(default_factory=list)
    niche: Optional[str] = None
    region: str = "miami"
    category: str = "services"
    
    def to_dict(self) -> Dict:
        return {
            "listing_id": self.listing_id,
            "title": self.title,
            "url": self.url,
            "price": self.price,
            "location": self.location,
            "neighborhood": self.neighborhood,
            "posted_date": self.posted_date,
            "body_text": self.body_text[:500] if self.body_text else None,
            "contact_phone": self.contact_phone,
            "contact_email": self.contact_email,
            "niche": self.niche,
            "region": self.region,
            "category": self.category
        }
    
    def generate_signal_id(self) -> str:
        """Generate a unique signal ID for deduplication."""
        unique_string = f"craigslist-{self.region}-{self.listing_id}"
        return hashlib.sha256(unique_string.encode()).hexdigest()[:16]


@dataclass
class CraigslistScanResult:
    """Result of a Craigslist scan."""
    success: bool
    listings: List[CraigslistListing] = field(default_factory=list)
    region: str = "miami"
    category: str = "services"
    niche: Optional[str] = None
    scan_time_ms: int = 0
    error: Optional[str] = None
    pages_scanned: int = 0


def log_craigslist(action: str, region: Optional[str] = None, details: Optional[Dict] = None) -> None:
    """Log Craigslist connector activity."""
    prefix = "[DRY_RUN]" if CRAIGSLIST_DRY_RUN else ""
    msg_parts = [f"{prefix}[CRAIGSLIST][{action.upper()}]"]
    if region:
        msg_parts.append(f"region={region}")
    if details:
        for k, v in details.items():
            if isinstance(v, str) and len(v) > 50:
                v = v[:50] + "..."
            msg_parts.append(f"{k}={v}")
    print(" | ".join(msg_parts))


def _detect_niche(title: str, body: Optional[str] = None) -> Optional[str]:
    """Detect the business niche from title and body text."""
    text = (title + " " + (body or "")).lower()
    
    for niche, keywords in NICHE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return niche
    
    return None


def _extract_phone(text: str) -> Optional[str]:
    """Extract phone number from text."""
    phone_pattern = re.compile(
        r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        re.IGNORECASE
    )
    match = phone_pattern.search(text)
    if match:
        return match.group(0)
    return None


def _extract_email(text: str) -> Optional[str]:
    """Extract email from text (usually anonymized on Craigslist)."""
    email_pattern = re.compile(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        re.IGNORECASE
    )
    match = email_pattern.search(text)
    if match:
        email = match.group(0)
        if "craigslist.org" not in email.lower():
            return email
    return None


def _extract_company_name(title: str, body: Optional[str] = None) -> Optional[str]:
    """Try to extract company name from listing."""
    text = title + " " + (body or "")
    
    patterns = [
        r'([A-Z][a-zA-Z]+(?:\s+[A-Z]?[a-zA-Z&\'\-]+){0,3}\s+(?:HVAC|Roofing|Plumbing|Electric|Landscaping|Pool|Cleaning|Painting|Moving|Construction|Services|Solutions|Inc|LLC|Corp|Co))',
        r'"([^"]{5,50})"',
        r'\*\*([^*]{5,50})\*\*',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) >= 5 and len(name) <= 60:
                return name
    
    return None


def _fetch_page(url: str, retries: int = 2) -> Optional[str]:
    """Fetch a Craigslist page with retry logic."""
    if CRAIGSLIST_DRY_RUN:
        log_craigslist("DRY_RUN_FETCH", details={"url": url[:60]})
        return None
    
    for attempt in range(retries):
        try:
            headers = {
                "User-Agent": USER_AGENTS[attempt % len(USER_AGENTS)],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
            
            response = requests.get(url, headers=headers, timeout=CRAIGSLIST_TIMEOUT)
            
            if response.status_code == 200:
                return response.text
            elif response.status_code == 429:
                log_craigslist("RATE_LIMITED", details={"attempt": attempt + 1})
                time.sleep(CRAIGSLIST_RATE_LIMIT * (attempt + 1) * 2)
                continue
            else:
                log_craigslist("HTTP_ERROR", details={"status": response.status_code})
                return None
                
        except (RequestException, Timeout) as e:
            if attempt < retries - 1:
                time.sleep(CRAIGSLIST_RATE_LIMIT)
                continue
            log_craigslist("FETCH_ERROR", details={"error": str(e)[:50]})
            return None
    
    return None


def _parse_listing_page(html: str, url: str, region: str) -> Optional[CraigslistListing]:
    """Parse a Craigslist listing detail page."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        listing_id_match = re.search(r'/(\d+)\.html', url)
        listing_id = listing_id_match.group(1) if listing_id_match else hashlib.md5(url.encode()).hexdigest()[:10]
        
        title_elem = soup.select_one('#titletextonly') or soup.select_one('.postingtitletext')
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"
        
        body_elem = soup.select_one('#postingbody')
        body_text = ""
        if body_elem:
            for script in body_elem.find_all('script'):
                script.decompose()
            body_text = body_elem.get_text(strip=True, separator=" ")
            body_text = re.sub(r'\s+', ' ', body_text)[:2000]
        
        price_elem = soup.select_one('.price')
        price = price_elem.get_text(strip=True) if price_elem else None
        
        location_elem = soup.select_one('.postingtitletext small')
        location = location_elem.get_text(strip=True).strip('()') if location_elem else None
        
        time_elem = soup.select_one('time.date.timeago')
        posted_date = str(time_elem.get('datetime')) if time_elem and time_elem.get('datetime') else None
        
        phone = _extract_phone(body_text)
        email = _extract_email(body_text)
        niche = _detect_niche(title, body_text)
        
        images = []
        for img in soup.select('.gallery img, .slide img')[:5]:
            src = img.get('src') or img.get('data-src')
            if src:
                images.append(src)
        
        return CraigslistListing(
            listing_id=listing_id,
            title=title,
            url=url,
            price=price,
            location=location,
            posted_date=posted_date,
            body_text=body_text,
            contact_phone=phone,
            contact_email=email,
            images=images,
            niche=niche,
            region=region
        )
        
    except Exception as e:
        log_craigslist("PARSE_ERROR", details={"error": str(e)[:50]})
        return None


def _parse_search_results(html: str, base_url: str) -> List[Dict]:
    """Parse Craigslist search results page."""
    results = []
    
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        for result in soup.select('.result-row, .cl-search-result, li.cl-static-search-result'):
            link = result.select_one('a.result-title, a.cl-app-anchor, a.titlestring')
            if not link:
                continue
            
            href_attr = link.get('href', '')
            href = str(href_attr) if href_attr else ''
            if not href or '/post/' in href:
                continue
            
            title = link.get_text(strip=True)
            
            if href.startswith('/'):
                href = urljoin(base_url, href)
            
            price_elem = result.select_one('.result-price, .priceinfo')
            price = price_elem.get_text(strip=True) if price_elem else None
            
            loc_elem = result.select_one('.result-hood, .nearby')
            location = loc_elem.get_text(strip=True).strip('()') if loc_elem else None
            
            results.append({
                "url": href,
                "title": title,
                "price": price,
                "location": location
            })
            
    except ImportError:
        log_craigslist("ERROR", details={"error": "BeautifulSoup not installed"})
    except Exception as e:
        log_craigslist("PARSE_ERROR", details={"error": str(e)[:50]})
    
    return results


def scan_region(
    region: str = "miami",
    category: str = "services",
    niche: Optional[str] = None,
    max_listings: int = 20,
    fetch_details: bool = True
) -> CraigslistScanResult:
    """
    Scan a Craigslist region for listings.
    
    Args:
        region: Target region (miami, fortlauderdale, palmbeach)
        category: Search category
        niche: Optional niche filter (hvac, plumbing, etc.)
        max_listings: Maximum listings to return
        fetch_details: Whether to fetch full listing details
        
    Returns:
        CraigslistScanResult with discovered listings
    """
    start_time = time.time()
    listings: List[CraigslistListing] = []
    pages_scanned = 0
    
    base_url = SOUTH_FLORIDA_REGIONS.get(region.lower(), SOUTH_FLORIDA_REGIONS["miami"])
    
    search_path = SIGNAL_CATEGORIES.get(category, "/search/sss")
    if niche and niche in SERVICE_CATEGORIES:
        search_path = SERVICE_CATEGORIES[niche]
    elif niche:
        search_path = f"/search/sss?query={quote(niche)}"
    
    search_url = f"{base_url}{search_path}"
    
    log_craigslist("SCAN_START", region, {
        "category": category,
        "niche": niche,
        "url": search_url[:60]
    })
    
    html = _fetch_page(search_url)
    if not html:
        return CraigslistScanResult(
            success=False,
            region=region,
            category=category,
            niche=niche,
            error="Failed to fetch search results"
        )
    
    pages_scanned += 1
    search_results = _parse_search_results(html, base_url)
    
    log_craigslist("SEARCH_RESULTS", region, {"count": len(search_results)})
    
    for result in search_results[:max_listings]:
        if fetch_details:
            time.sleep(CRAIGSLIST_RATE_LIMIT)
            
            detail_html = _fetch_page(result["url"])
            if detail_html:
                pages_scanned += 1
                listing = _parse_listing_page(detail_html, result["url"], region)
                if listing:
                    listing.category = category
                    if niche:
                        listing.niche = niche
                    listings.append(listing)
        else:
            listing_id = re.search(r'/(\d+)\.html', result["url"])
            listing = CraigslistListing(
                listing_id=listing_id.group(1) if listing_id else hashlib.md5(result["url"].encode()).hexdigest()[:10],
                title=result["title"],
                url=result["url"],
                price=result.get("price"),
                location=result.get("location"),
                region=region,
                category=category,
                niche=niche or _detect_niche(result["title"])
            )
            listings.append(listing)
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    log_craigslist("SCAN_COMPLETE", region, {
        "listings": len(listings),
        "pages": pages_scanned,
        "time_ms": elapsed_ms
    })
    
    return CraigslistScanResult(
        success=True,
        listings=listings,
        region=region,
        category=category,
        niche=niche,
        scan_time_ms=elapsed_ms,
        pages_scanned=pages_scanned
    )


def scan_all_regions(
    category: str = "services",
    niche: Optional[str] = None,
    max_per_region: int = 10
) -> List[CraigslistListing]:
    """
    Scan all South Florida regions for listings.
    
    Args:
        category: Search category
        niche: Optional niche filter
        max_per_region: Max listings per region
        
    Returns:
        Combined list of listings from all regions
    """
    all_listings: List[CraigslistListing] = []
    
    for region in SOUTH_FLORIDA_REGIONS.keys():
        result = scan_region(
            region=region,
            category=category,
            niche=niche,
            max_listings=max_per_region,
            fetch_details=True
        )
        
        if result.success:
            all_listings.extend(result.listings)
        
        time.sleep(CRAIGSLIST_RATE_LIMIT * 2)
    
    return all_listings


def convert_to_signal(listing: CraigslistListing) -> Dict:
    """
    Convert a Craigslist listing to a Signal-compatible dict.
    
    Returns a dict ready for Signal model creation.
    """
    company_name = _extract_company_name(listing.title, listing.body_text)
    
    return {
        "source_type": "craigslist",
        "raw_payload": json.dumps(listing.to_dict()),
        "context_summary": listing.title,
        "geography": listing.region.title() if listing.region else "Miami",
        "extracted_contact_info": json.dumps({
            "extracted_emails": [listing.contact_email] if listing.contact_email else [],
            "extracted_phones": [listing.contact_phone] if listing.contact_phone else [],
            "extracted_urls": [listing.url],
            "source_confidence": 0.7 if listing.contact_phone else 0.5
        }),
        "metadata": {
            "listing_id": listing.listing_id,
            "signal_id": listing.generate_signal_id(),
            "niche": listing.niche,
            "category": listing.category,
            "price": listing.price,
            "location": listing.location,
            "company_name": company_name,
            "posted_date": listing.posted_date
        }
    }


def generate_lead_event(listing: CraigslistListing) -> Dict:
    """
    Generate LeadEvent-compatible data from a Craigslist listing.
    
    Returns a dict ready for LeadEvent model creation.
    """
    company_name = _extract_company_name(listing.title, listing.body_text)
    niche = listing.niche or _detect_niche(listing.title, listing.body_text)
    
    category_map = {
        "hvac": "growth_opportunity",
        "plumbing": "growth_opportunity",
        "roofing": "growth_opportunity",
        "construction": "growth_opportunity",
        "business_for_sale": "market_entry",
        "services_offered": "competitor_intel",
    }
    
    category = "growth_opportunity"
    if niche and niche in category_map:
        category = category_map[niche]
    elif listing.category in category_map:
        category = category_map[listing.category]
    
    return {
        "lead_company": company_name,
        "lead_phone_raw": listing.contact_phone,
        "lead_email": listing.contact_email,
        "summary": f"[Craigslist] {listing.title} - {listing.location or listing.region}",
        "category": category,
        "urgency_score": 65 if listing.contact_phone else 55,
        "recommended_action": f"Potential {niche or 'service'} opportunity in {listing.region}. Contact to explore partnership or service needs.",
        "enrichment_status": "UNENRICHED",
        "metadata": {
            "source": "craigslist",
            "listing_id": listing.listing_id,
            "niche": niche,
            "region": listing.region,
            "url": listing.url
        }
    }
