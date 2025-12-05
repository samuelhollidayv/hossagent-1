"""
Job Board Connector for HossAgent SignalNet - EPIC 3.2

Ingests job postings from public job boards to identify SMBs that are hiring.
Hiring signals indicate business growth and potential need for B2B services.

Target regions: South Florida (Miami-Dade, Broward, Palm Beach)
Target niches: HVAC, plumbing, roofing, electrical, landscaping, trades

Pure web scraping - NO paid APIs.
"""

import os
import re
import json
import time
import hashlib
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, quote_plus

import requests
from sqlmodel import Session, select

from models import Signal, LeadEvent, ENRICHMENT_STATUS_UNENRICHED


REGIONS = {
    "miami": {
        "name": "Miami",
        "geography": "Miami-Dade County, Florida",
        "zip_codes": ["33101", "33125", "33130", "33132", "33136", "33139", "33142", "33145"],
    },
    "fort_lauderdale": {
        "name": "Fort Lauderdale",
        "geography": "Broward County, Florida",
        "zip_codes": ["33301", "33304", "33305", "33306", "33308", "33309", "33311"],
    },
    "west_palm": {
        "name": "West Palm Beach",
        "geography": "Palm Beach County, Florida",
        "zip_codes": ["33401", "33403", "33405", "33407", "33409", "33411"],
    },
}

TRADE_NICHES = {
    "hvac": ["hvac", "air conditioning", "heating", "ac technician", "refrigeration"],
    "plumbing": ["plumber", "plumbing", "pipe fitter", "drain", "water heater"],
    "roofing": ["roofing", "roofer", "shingle", "roof repair", "roof installer"],
    "electrical": ["electrician", "electrical", "wiring", "electrical contractor"],
    "landscaping": ["landscaping", "landscaper", "lawn care", "irrigation", "tree service"],
    "construction": ["construction", "contractor", "general contractor", "builder", "framing"],
    "painting": ["painter", "painting contractor", "commercial painting"],
    "pool": ["pool service", "pool technician", "pool maintenance", "pool installer"],
    "cleaning": ["cleaning service", "janitorial", "commercial cleaning", "pressure washing"],
    "pest_control": ["pest control", "exterminator", "termite", "fumigation"],
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

REQUEST_TIMEOUT = 15
MIN_DELAY = 2.0
MAX_DELAY = 5.0
MAX_RETRIES = 2
RATE_LIMIT_COOLDOWN = 300

_consecutive_failures = 0
_last_failure_time = 0.0
_request_cache: Dict[str, Tuple[str, float]] = {}
CACHE_TTL = 3600


@dataclass
class JobPosting:
    """A single job posting extracted from a job board."""
    title: str
    company_name: str
    location: str
    description: Optional[str] = None
    url: Optional[str] = None
    posted_date: Optional[str] = None
    niche: Optional[str] = None
    geography: Optional[str] = None
    source: str = "job_board"
    
    def to_signal_data(self) -> Dict:
        """Convert to signal data format."""
        return {
            "title": self.title,
            "company_name": self.company_name,
            "location": self.location,
            "description": self.description or "",
            "url": self.url or "",
            "posted_date": self.posted_date or "",
            "niche": self.niche or "",
            "geography": self.geography or "",
            "source": self.source,
        }


def _get_random_user_agent() -> str:
    """Get a random user agent for requests."""
    return random.choice(USER_AGENTS)


def _get_cached_response(url: str) -> Optional[str]:
    """Get cached response if still valid."""
    if url in _request_cache:
        content, timestamp = _request_cache[url]
        if time.time() - timestamp < CACHE_TTL:
            return content
    return None


def _cache_response(url: str, content: str) -> None:
    """Cache response content."""
    _request_cache[url] = (content, time.time())


def _should_backoff() -> bool:
    """Check if we should back off due to rate limiting."""
    global _consecutive_failures, _last_failure_time
    
    if _consecutive_failures >= 3:
        time_since_failure = time.time() - _last_failure_time
        if time_since_failure < RATE_LIMIT_COOLDOWN:
            print(f"[JOB_BOARD][BACKOFF] Waiting {int(RATE_LIMIT_COOLDOWN - time_since_failure)}s before retry")
            return True
        else:
            _consecutive_failures = 0
    return False


def _record_failure() -> None:
    """Record a failure for backoff tracking."""
    global _consecutive_failures, _last_failure_time
    _consecutive_failures += 1
    _last_failure_time = time.time()


def _record_success() -> None:
    """Record success to reset failure count."""
    global _consecutive_failures
    _consecutive_failures = 0


def _fetch_page(url: str, retries: int = MAX_RETRIES) -> Optional[str]:
    """Fetch a page with caching and rate limiting."""
    if _should_backoff():
        return None
    
    cached = _get_cached_response(url)
    if cached:
        print(f"[JOB_BOARD][CACHE_HIT] {url[:60]}")
        return cached
    
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay)
    
    headers = {
        "User-Agent": _get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            
            if response.status_code == 200:
                _record_success()
                _cache_response(url, response.text)
                return response.text
            elif response.status_code == 429:
                _record_failure()
                print(f"[JOB_BOARD][RATE_LIMITED] {url[:60]}")
                return None
            elif response.status_code >= 400:
                print(f"[JOB_BOARD][HTTP_{response.status_code}] {url[:60]}")
                if attempt < retries - 1:
                    time.sleep(delay * (attempt + 1))
                    continue
                return None
                
        except requests.exceptions.Timeout:
            print(f"[JOB_BOARD][TIMEOUT] {url[:60]}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
                continue
        except requests.exceptions.RequestException as e:
            print(f"[JOB_BOARD][ERROR] {url[:60]}: {str(e)[:50]}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
                continue
    
    _record_failure()
    return None


def _detect_niche(text: str) -> Optional[str]:
    """Detect the trade niche from job posting text."""
    text_lower = text.lower()
    
    for niche, keywords in TRADE_NICHES.items():
        for keyword in keywords:
            if keyword in text_lower:
                return niche
    
    return None


def _extract_company_name(html: str, title: str) -> Optional[str]:
    """Extract company name from job posting HTML."""
    patterns = [
        r'data-company="([^"]+)"',
        r'"companyName"\s*:\s*"([^"]+)"',
        r'"employer"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
        r'class="[^"]*company[^"]*"[^>]*>([^<]+)<',
        r'<span[^>]*class="[^"]*employer[^"]*"[^>]*>([^<]+)<',
        r'hiring organization.*?<[^>]+>([^<]+)<',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            name = match.group(1).strip()
            if len(name) > 2 and len(name) < 100:
                name = re.sub(r'<[^>]+>', '', name)
                name = name.strip()
                if name:
                    return name
    
    return None


def _extract_location(html: str) -> Optional[str]:
    """Extract location from job posting HTML."""
    patterns = [
        r'data-location="([^"]+)"',
        r'"jobLocation"\s*:\s*\{[^}]*"addressLocality"\s*:\s*"([^"]+)"',
        r'"location"\s*:\s*"([^"]+)"',
        r'class="[^"]*location[^"]*"[^>]*>([^<]+)<',
        r'Miami|Fort Lauderdale|West Palm Beach|Broward|Palm Beach|Dade',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            if isinstance(match.group(0), str) and match.group(0) in ["Miami", "Fort Lauderdale", "West Palm Beach", "Broward", "Palm Beach", "Dade"]:
                return match.group(0)
            location = match.group(1).strip() if match.lastindex else match.group(0)
            return location
    
    return None


def _parse_indeed_html(html: str, region: str) -> List[JobPosting]:
    """Parse Indeed search results HTML for job postings."""
    jobs = []
    
    job_card_pattern = r'<div[^>]*class="[^"]*job_seen_beacon[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>'
    
    title_pattern = r'<h2[^>]*class="[^"]*jobTitle[^"]*"[^>]*>.*?<a[^>]*>.*?<span[^>]*>([^<]+)</span>'
    company_pattern = r'<span[^>]*data-testid="company-name"[^>]*>([^<]+)</span>'
    location_pattern = r'<div[^>]*data-testid="text-location"[^>]*>([^<]+)</div>'
    
    titles = re.findall(title_pattern, html, re.IGNORECASE | re.DOTALL)
    companies = re.findall(company_pattern, html, re.IGNORECASE)
    locations = re.findall(location_pattern, html, re.IGNORECASE)
    
    min_len = min(len(titles), len(companies), len(locations))
    
    region_info = REGIONS.get(region, {})
    geography = region_info.get("geography", "South Florida")
    
    for i in range(min_len):
        title = titles[i].strip()
        company = companies[i].strip()
        location = locations[i].strip()
        
        niche = _detect_niche(f"{title} {company}")
        
        if niche:
            jobs.append(JobPosting(
                title=title,
                company_name=company,
                location=location,
                niche=niche,
                geography=geography,
                source="indeed",
            ))
    
    return jobs


def _parse_glassdoor_html(html: str, region: str) -> List[JobPosting]:
    """Parse Glassdoor search results HTML for job postings."""
    jobs = []
    
    job_pattern = r'<li[^>]*class="[^"]*JobsList_jobListItem[^"]*"[^>]*>(.*?)</li>'
    title_pattern = r'<a[^>]*class="[^"]*JobCard_jobTitle[^"]*"[^>]*>([^<]+)</a>'
    company_pattern = r'<span[^>]*class="[^"]*EmployerProfile_compactEmployerName[^"]*"[^>]*>([^<]+)</span>'
    
    region_info = REGIONS.get(region, {})
    geography = region_info.get("geography", "South Florida")
    
    for job_match in re.finditer(job_pattern, html, re.IGNORECASE | re.DOTALL):
        job_html = job_match.group(1)
        
        title_match = re.search(title_pattern, job_html, re.IGNORECASE)
        company_match = re.search(company_pattern, job_html, re.IGNORECASE)
        
        if title_match and company_match:
            title = title_match.group(1).strip()
            company = company_match.group(1).strip()
            
            niche = _detect_niche(f"{title} {company}")
            
            if niche:
                jobs.append(JobPosting(
                    title=title,
                    company_name=company,
                    location=region_info.get("name", "South Florida"),
                    niche=niche,
                    geography=geography,
                    source="glassdoor",
                ))
    
    return jobs


def _search_duckduckgo_jobs(query: str, region: str) -> List[JobPosting]:
    """
    Search DuckDuckGo for job postings as a fallback.
    More reliable than scraping Indeed/Glassdoor directly.
    """
    jobs = []
    
    region_info = REGIONS.get(region, {})
    location = region_info.get("name", "Miami")
    geography = region_info.get("geography", "South Florida")
    
    search_query = f"{query} jobs {location} Florida hiring"
    encoded_query = quote_plus(search_query)
    
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
    
    html = _fetch_page(url)
    if not html:
        return jobs
    
    result_pattern = r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>'
    
    for match in re.finditer(result_pattern, html, re.IGNORECASE):
        url = match.group(1)
        title = match.group(2).strip()
        
        if 'indeed.com' in url.lower() or 'glassdoor.com' in url.lower() or 'linkedin.com/jobs' in url.lower():
            company_match = re.search(r'at\s+([^-]+?)(?:\s*-|\s*\||\s*$)', title, re.IGNORECASE)
            if company_match:
                company = company_match.group(1).strip()
            else:
                company = None
            
            niche = _detect_niche(title)
            
            if niche and company:
                jobs.append(JobPosting(
                    title=title,
                    company_name=company,
                    location=location,
                    url=url,
                    niche=niche,
                    geography=geography,
                    source="duckduckgo_jobs",
                ))
    
    return jobs


def fetch_job_postings(
    niches: Optional[List[str]] = None,
    regions: Optional[List[str]] = None,
    max_per_niche: int = 5
) -> List[JobPosting]:
    """
    Fetch job postings from multiple sources.
    
    Uses DuckDuckGo search as the primary method since direct scraping
    of Indeed/Glassdoor is heavily rate-limited.
    
    Args:
        niches: List of trade niches to search (default: all)
        regions: List of regions to search (default: all South Florida)
        max_per_niche: Maximum jobs per niche per region
    
    Returns:
        List of JobPosting objects
    """
    if niches is None:
        niches = list(TRADE_NICHES.keys())[:5]
    
    if regions is None:
        regions = list(REGIONS.keys())
    
    all_jobs = []
    seen_companies = set()
    
    for region in regions:
        for niche in niches:
            if _should_backoff():
                print(f"[JOB_BOARD][SKIP] Backing off - skipping {niche} in {region}")
                continue
            
            keywords = TRADE_NICHES.get(niche, [niche])
            primary_keyword = keywords[0] if keywords else niche
            
            print(f"[JOB_BOARD][SEARCH] {primary_keyword} jobs in {region}")
            
            jobs = _search_duckduckgo_jobs(primary_keyword, region)
            
            added = 0
            for job in jobs:
                if added >= max_per_niche:
                    break
                
                company_key = f"{job.company_name.lower()}:{job.geography}"
                if company_key in seen_companies:
                    continue
                
                seen_companies.add(company_key)
                all_jobs.append(job)
                added += 1
            
            print(f"[JOB_BOARD][FOUND] {added} unique jobs for {primary_keyword} in {region}")
    
    return all_jobs


def create_signals_from_jobs(
    session: Session,
    jobs: List[JobPosting],
    dry_run: bool = False
) -> List[Signal]:
    """
    Create Signal records from job postings.
    
    Args:
        session: Database session
        jobs: List of JobPosting objects
        dry_run: If True, don't persist to database
    
    Returns:
        List of created Signal objects
    """
    signals = []
    
    for job in jobs:
        signal_hash = hashlib.md5(
            f"{job.company_name}:{job.title}:{job.geography}".encode()
        ).hexdigest()[:16]
        
        existing = session.exec(
            select(Signal).where(Signal.source_ref == signal_hash)
        ).first()
        
        if existing:
            print(f"[JOB_BOARD][DUP] Signal already exists: {job.company_name}")
            continue
        
        summary = f"{job.company_name} is hiring: {job.title} in {job.location}"
        if job.niche:
            summary += f" ({job.niche.upper()} sector)"
        
        signal = Signal(
            source_type="job_board",
            source_ref=signal_hash,
            source_url=job.url or "",
            context_summary=summary,
            geography=job.geography or "South Florida",
            niche=job.niche or "",
            raw_data=json.dumps(job.to_signal_data()),
            score=70,
        )
        
        if not dry_run:
            session.add(signal)
            signals.append(signal)
            print(f"[JOB_BOARD][SIGNAL] Created: {job.company_name} - {job.title}")
        else:
            print(f"[JOB_BOARD][DRY_RUN] Would create: {job.company_name} - {job.title}")
    
    if not dry_run and signals:
        session.commit()
    
    return signals


def create_lead_events_from_signals(
    session: Session,
    signals: List[Signal],
    dry_run: bool = False
) -> List[LeadEvent]:
    """
    Create LeadEvent records from job board signals.
    
    Args:
        session: Database session
        signals: List of Signal objects
        dry_run: If True, don't persist to database
    
    Returns:
        List of created LeadEvent objects
    """
    lead_events = []
    
    for signal in signals:
        try:
            raw_data = json.loads(signal.raw_data) if signal.raw_data else {}
        except json.JSONDecodeError:
            raw_data = {}
        
        company_name = raw_data.get("company_name", "Unknown Company")
        niche = raw_data.get("niche", signal.niche or "trades")
        
        lead_event = LeadEvent(
            signal_id=signal.id,
            lead_company=company_name,
            summary=signal.context_summary,
            category="JOB_POSTING",
            urgency_score=70,
            status="NEW",
            enrichment_status=ENRICHMENT_STATUS_UNENRICHED,
            recommended_action=f"Hiring signal detected for {niche.upper()} company. Research and reach out with relevant B2B services.",
        )
        
        if not dry_run:
            session.add(lead_event)
            lead_events.append(lead_event)
            print(f"[JOB_BOARD][LEAD_EVENT] Created for: {company_name}")
        else:
            print(f"[JOB_BOARD][DRY_RUN] Would create LeadEvent for: {company_name}")
    
    if not dry_run and lead_events:
        session.commit()
    
    return lead_events


def run_job_board_ingestion(
    session: Session,
    niches: Optional[List[str]] = None,
    regions: Optional[List[str]] = None,
    max_per_niche: int = 3,
    dry_run: bool = False
) -> Dict:
    """
    Main entry point for job board ingestion.
    
    Args:
        session: Database session
        niches: Trade niches to search
        regions: Regions to search
        max_per_niche: Max jobs per niche per region
        dry_run: Skip database writes
    
    Returns:
        Summary dict with counts
    """
    print("[JOB_BOARD][START] Beginning job board ingestion")
    
    jobs = fetch_job_postings(niches, regions, max_per_niche)
    print(f"[JOB_BOARD][FETCH] Found {len(jobs)} job postings")
    
    signals = create_signals_from_jobs(session, jobs, dry_run)
    print(f"[JOB_BOARD][SIGNALS] Created {len(signals)} signals")
    
    lead_events = create_lead_events_from_signals(session, signals, dry_run)
    print(f"[JOB_BOARD][LEAD_EVENTS] Created {len(lead_events)} lead events")
    
    result = {
        "jobs_found": len(jobs),
        "signals_created": len(signals),
        "lead_events_created": len(lead_events),
        "dry_run": dry_run,
    }
    
    print(f"[JOB_BOARD][COMPLETE] {result}")
    return result


print("[JOB_BOARD][STARTUP] Job Board Connector loaded - EPIC 3.2")
