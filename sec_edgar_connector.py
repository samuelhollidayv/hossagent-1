"""
SEC EDGAR Connector for HossAgent MacroStorm - EPIC 5

Polls SEC EDGAR for public filings (10-K, 10-Q, 8-K) and extracts
strategic intelligence about big-company moves.

These filings reveal:
- Expansion plans (new locations, markets)
- Contraction signals (layoffs, closures)
- Supply chain changes
- Geographic focus
- Risk factors

EDGAR API: https://www.sec.gov/cgi-bin/browse-edgar
RSS Feed: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=10-K&output=atom

Pure web scraping - NO paid APIs.
"""

import json
import os
import re
import time
import hashlib
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, quote_plus
import xml.etree.ElementTree as ET

import requests
from sqlmodel import Session, select

from models import (
    MacroEvent,
    MACRO_SOURCE_SEC_10K,
    MACRO_SOURCE_SEC_10Q,
    MACRO_SOURCE_SEC_8K,
    MACRO_FORCE_TYPE_EXPANSION,
    MACRO_FORCE_TYPE_CONTRACTION,
    MACRO_FORCE_TYPE_RESTRUCTURING,
    MACRO_FORCE_TYPE_MERGER,
    MACRO_FORCE_TYPE_BANKRUPTCY,
    MACRO_FORCE_TYPE_SUPPLY_CHAIN,
    MACRO_FORCE_TYPE_REGULATORY,
)


SEC_BASE_URL = "https://www.sec.gov"
EDGAR_COMPANY_SEARCH = "https://www.sec.gov/cgi-bin/browse-edgar"
EDGAR_FULL_TEXT_SEARCH = "https://efts.sec.gov/LATEST/search-index"

FILING_TYPES = {
    "10-K": MACRO_SOURCE_SEC_10K,
    "10-Q": MACRO_SOURCE_SEC_10Q,
    "8-K": MACRO_SOURCE_SEC_8K,
}

TRACKED_TICKERS = [
    "MCD",
    "WMT",
    "HD",
    "LOW",
    "TGT",
    "COST",
    "DG",
    "DLTR",
    "KR",
    "SBUX",
    "CMG",
    "DPZ",
    "YUM",
    "QSR",
    "DRI",
    "DENN",
    "WEN",
    "SHAK",
    "CAVA",
    "WING",
]

FLORIDA_KEYWORDS = [
    "florida",
    "miami",
    "fort lauderdale",
    "broward",
    "palm beach",
    "orlando",
    "tampa",
    "jacksonville",
    "southeast",
    "sunbelt",
]

EXPANSION_KEYWORDS = [
    "new store",
    "new location",
    "new unit",
    "expansion",
    "expand",
    "opening",
    "open",
    "growth",
    "develop",
    "construction",
    "build",
    "capital expenditure",
    "capex",
    "investment",
    "increase capacity",
    "add capacity",
    "new market",
    "enter",
    "launch",
]

CONTRACTION_KEYWORDS = [
    "close",
    "closure",
    "closing",
    "restructur",
    "layoff",
    "workforce reduction",
    "downsize",
    "consolidat",
    "exit",
    "discontinue",
    "impairment",
    "write-off",
    "write-down",
    "decline",
    "decrease",
    "reduce",
    "cut",
]

SUPPLY_CHAIN_KEYWORDS = [
    "supply chain",
    "supplier",
    "vendor",
    "distribution",
    "logistics",
    "warehouse",
    "inventory",
    "procurement",
    "sourcing",
    "manufacturing",
]

USER_AGENT = "HossAgent/1.0 (contact@hossagent.net)"

REQUEST_TIMEOUT = 30
MIN_DELAY = 0.5
MAX_DELAY = 1.0
MAX_RETRIES = 2
CACHE_TTL = 86400

_request_cache: Dict[str, Tuple[str, float]] = {}


@dataclass
class SECFiling:
    """A single SEC filing."""
    cik: str
    company_name: str
    ticker: Optional[str]
    filing_type: str
    filed_date: str
    accession_number: str
    url: str
    description: Optional[str] = None
    raw_content: Optional[str] = None


@dataclass
class ExtractedIntelligence:
    """Extracted intelligence from a filing."""
    force_type: str
    headline: str
    geographies: List[str]
    segments_affected: List[str]
    time_horizon: Optional[str]
    raw_snippet: str
    confidence: float


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


def _fetch_sec_page(url: str, retries: int = MAX_RETRIES) -> Optional[str]:
    """Fetch a page from SEC with proper rate limiting."""
    cached = _get_cached_response(url)
    if cached:
        print(f"[SEC_EDGAR][CACHE_HIT] {url[:60]}")
        return cached
    
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay)
    
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            
            if response.status_code == 200:
                _cache_response(url, response.text)
                return response.text
            elif response.status_code == 429:
                print(f"[SEC_EDGAR][RATE_LIMITED] Waiting 60s...")
                time.sleep(60)
                continue
            else:
                print(f"[SEC_EDGAR][HTTP_{response.status_code}] {url[:60]}")
                if attempt < retries - 1:
                    time.sleep(delay * (attempt + 1))
                    continue
                    
        except requests.exceptions.RequestException as e:
            print(f"[SEC_EDGAR][ERROR] {url[:60]}: {str(e)[:50]}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
                continue
    
    return None


def _parse_rss_feed(xml_content: str) -> List[Dict]:
    """Parse SEC RSS feed for filings."""
    entries = []
    
    try:
        root = ET.fromstring(xml_content)
        
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "edgar": "https://www.sec.gov/cgi-bin/browse-edgar"
        }
        
        for entry in root.findall(".//atom:entry", ns):
            title_elem = entry.find("atom:title", ns)
            link_elem = entry.find("atom:link", ns)
            updated_elem = entry.find("atom:updated", ns)
            summary_elem = entry.find("atom:summary", ns)
            
            if title_elem is not None and link_elem is not None:
                entries.append({
                    "title": title_elem.text or "",
                    "link": link_elem.get("href", ""),
                    "updated": updated_elem.text if updated_elem is not None else "",
                    "summary": summary_elem.text if summary_elem is not None else "",
                })
    
    except ET.ParseError as e:
        print(f"[SEC_EDGAR][XML_ERROR] Failed to parse RSS: {str(e)[:50]}")
    
    return entries


def _extract_company_info(title: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract company name, CIK, and ticker from filing title."""
    cik_match = re.search(r'\((\d{10})\)', title)
    cik = cik_match.group(1) if cik_match else None
    
    company_match = re.search(r'^([^(]+)', title)
    company_name = company_match.group(1).strip() if company_match else None
    
    ticker_match = re.search(r'\(([A-Z]{1,5})\)', title)
    ticker = ticker_match.group(1) if ticker_match else None
    
    return company_name, cik, ticker


def _detect_force_type(text: str) -> Tuple[str, float]:
    """Detect the force type from filing text."""
    text_lower = text.lower()
    
    scores = {
        MACRO_FORCE_TYPE_EXPANSION: 0,
        MACRO_FORCE_TYPE_CONTRACTION: 0,
        MACRO_FORCE_TYPE_SUPPLY_CHAIN: 0,
        MACRO_FORCE_TYPE_RESTRUCTURING: 0,
    }
    
    for keyword in EXPANSION_KEYWORDS:
        if keyword in text_lower:
            scores[MACRO_FORCE_TYPE_EXPANSION] += 1
    
    for keyword in CONTRACTION_KEYWORDS:
        if keyword in text_lower:
            scores[MACRO_FORCE_TYPE_CONTRACTION] += 1
    
    for keyword in SUPPLY_CHAIN_KEYWORDS:
        if keyword in text_lower:
            scores[MACRO_FORCE_TYPE_SUPPLY_CHAIN] += 1
    
    if "restructur" in text_lower or "reorganiz" in text_lower:
        scores[MACRO_FORCE_TYPE_RESTRUCTURING] += 2
    
    max_type = max(scores, key=scores.get)
    max_score = scores[max_type]
    
    if max_score == 0:
        return MACRO_FORCE_TYPE_EXPANSION, 0.3
    
    total = sum(scores.values())
    confidence = max_score / total if total > 0 else 0.3
    
    return max_type, min(0.95, confidence)


def _detect_geographies(text: str) -> List[str]:
    """Detect Florida/South Florida geographies in text."""
    text_lower = text.lower()
    found = []
    
    for keyword in FLORIDA_KEYWORDS:
        if keyword in text_lower:
            if keyword == "florida":
                found.append("Florida")
            elif keyword == "miami":
                found.append("Miami")
            elif keyword == "fort lauderdale":
                found.append("Fort Lauderdale")
            elif keyword == "broward":
                found.append("Broward County")
            elif keyword == "palm beach":
                found.append("Palm Beach County")
            elif keyword == "southeast" or keyword == "sunbelt":
                found.append("Southeast US")
    
    return list(set(found)) if found else ["National"]


def _extract_segments(text: str, force_type: str) -> List[str]:
    """Extract affected business segments from text."""
    segments = []
    text_lower = text.lower()
    
    segment_keywords = {
        "restaurant": "QSR",
        "food service": "Food Service",
        "retail": "Retail",
        "store": "Retail",
        "distribution": "Distribution",
        "logistics": "Logistics",
        "warehouse": "Warehousing",
        "real estate": "Real Estate",
        "construction": "Construction",
        "staffing": "Staffing",
        "labor": "Labor",
    }
    
    for keyword, segment in segment_keywords.items():
        if keyword in text_lower and segment not in segments:
            segments.append(segment)
    
    return segments if segments else ["General"]


def _extract_time_horizon(text: str) -> Optional[str]:
    """Extract time horizon from filing text."""
    patterns = [
        (r'over the next (\d+)\s*(?:to\s*\d+)?\s*years?', lambda m: f"{m.group(1)}-year"),
        (r'(\d+)\s*(?:to\s*\d+)?\s*year', lambda m: f"{m.group(1)}-year"),
        (r'fiscal (\d{4})', lambda m: f"FY{m.group(1)}"),
        (r'next\s*(\d+)\s*months?', lambda m: f"{m.group(1)}-months"),
        (r'(\d{4})', lambda m: f"By {m.group(1)}"),
    ]
    
    for pattern, formatter in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return formatter(match)
    
    return None


def _generate_headline(filing: SECFiling, force_type: str, snippet: str) -> str:
    """Generate a human-readable headline from filing intelligence."""
    action_words = {
        MACRO_FORCE_TYPE_EXPANSION: "expanding",
        MACRO_FORCE_TYPE_CONTRACTION: "contracting operations",
        MACRO_FORCE_TYPE_SUPPLY_CHAIN: "changing supply chain",
        MACRO_FORCE_TYPE_RESTRUCTURING: "restructuring",
        MACRO_FORCE_TYPE_MERGER: "merger activity",
        MACRO_FORCE_TYPE_BANKRUPTCY: "bankruptcy proceedings",
    }
    
    action = action_words.get(force_type, "strategic move")
    
    numbers = re.findall(r'\b(\d+)\s*(?:new\s+)?(?:store|location|unit|restaurant)', snippet, re.IGNORECASE)
    if numbers:
        return f"{filing.company_name} plans {numbers[0]} new locations ({filing.filing_type})"
    
    return f"{filing.company_name} {action} per {filing.filing_type} filing"


def fetch_recent_filings(
    filing_types: Optional[List[str]] = None,
    tickers: Optional[List[str]] = None,
    days_back: int = 30
) -> List[SECFiling]:
    """
    Fetch recent SEC filings from EDGAR.
    
    Args:
        filing_types: List of filing types (10-K, 10-Q, 8-K)
        tickers: List of stock tickers to monitor
        days_back: How many days back to search
    
    Returns:
        List of SECFiling objects
    """
    if filing_types is None:
        filing_types = ["10-K", "10-Q", "8-K"]
    
    if tickers is None:
        tickers = TRACKED_TICKERS[:10]
    
    filings = []
    
    for filing_type in filing_types:
        url = f"{EDGAR_COMPANY_SEARCH}?action=getcurrent&type={filing_type}&company=&dateb=&owner=include&count=40&output=atom"
        
        print(f"[SEC_EDGAR][FETCH] {filing_type} filings")
        
        xml_content = _fetch_sec_page(url)
        if not xml_content:
            continue
        
        entries = _parse_rss_feed(xml_content)
        
        for entry in entries:
            title = entry.get("title", "")
            company_name, cik, ticker = _extract_company_info(title)
            
            if not company_name:
                continue
            
            if tickers and ticker and ticker not in tickers:
                continue
            
            accession = ""
            link = entry.get("link", "")
            acc_match = re.search(r'/Archives/edgar/data/\d+/(\d+-\d+-\d+)', link)
            if acc_match:
                accession = acc_match.group(1)
            
            filing = SECFiling(
                cik=cik or "",
                company_name=company_name,
                ticker=ticker,
                filing_type=filing_type,
                filed_date=entry.get("updated", "")[:10],
                accession_number=accession,
                url=link,
                description=entry.get("summary", ""),
            )
            filings.append(filing)
            
            print(f"[SEC_EDGAR][FOUND] {company_name} - {filing_type}")
    
    return filings


def extract_intelligence_from_filing(filing: SECFiling) -> Optional[ExtractedIntelligence]:
    """
    Extract strategic intelligence from a filing.
    
    For full text extraction, we would fetch the actual filing document.
    This simplified version uses the description/summary.
    """
    text = f"{filing.description or ''} {filing.company_name}"
    
    if not any(kw in text.lower() for kw in FLORIDA_KEYWORDS):
        if not any(kw in text.lower() for kw in EXPANSION_KEYWORDS + CONTRACTION_KEYWORDS):
            return None
    
    force_type, confidence = _detect_force_type(text)
    geographies = _detect_geographies(text)
    segments = _extract_segments(text, force_type)
    time_horizon = _extract_time_horizon(text)
    
    headline = _generate_headline(filing, force_type, text)
    
    return ExtractedIntelligence(
        force_type=force_type,
        headline=headline,
        geographies=geographies,
        segments_affected=segments,
        time_horizon=time_horizon,
        raw_snippet=text[:500],
        confidence=confidence,
    )


def create_macro_events_from_filings(
    session: Session,
    filings: List[SECFiling],
    dry_run: bool = False
) -> List[MacroEvent]:
    """
    Create MacroEvent records from SEC filings.
    
    Args:
        session: Database session
        filings: List of SECFiling objects
        dry_run: If True, don't persist to database
    
    Returns:
        List of created MacroEvent objects
    """
    macro_events = []
    
    for filing in filings:
        intel = extract_intelligence_from_filing(filing)
        if not intel:
            print(f"[SEC_EDGAR][SKIP] No actionable intelligence: {filing.company_name}")
            continue
        
        event_id = f"macro-SEC-{filing.ticker or 'UNK'}-{filing.filing_type}-{filing.accession_number}"
        
        existing = session.exec(
            select(MacroEvent).where(MacroEvent.macro_event_id == event_id)
        ).first()
        
        if existing:
            print(f"[SEC_EDGAR][DUP] MacroEvent already exists: {event_id}")
            continue
        
        source_type = FILING_TYPES.get(filing.filing_type, MACRO_SOURCE_SEC_10K)
        
        macro_event = MacroEvent(
            macro_event_id=event_id,
            source_type=source_type,
            source_ref=filing.accession_number,
            source_url=filing.url,
            company_name=filing.company_name,
            ticker=filing.ticker,
            headline=intel.headline,
            geographies=json.dumps(intel.geographies),
            segments_affected=json.dumps(intel.segments_affected),
            force_type=intel.force_type,
            time_horizon=intel.time_horizon,
            raw_snippet=intel.raw_snippet,
            confidence=intel.confidence,
        )
        
        if not dry_run:
            session.add(macro_event)
            macro_events.append(macro_event)
            print(f"[SEC_EDGAR][MACRO_EVENT] Created: {intel.headline}")
        else:
            print(f"[SEC_EDGAR][DRY_RUN] Would create: {intel.headline}")
    
    if not dry_run and macro_events:
        session.commit()
    
    return macro_events


def run_sec_edgar_ingestion(
    session: Session,
    filing_types: Optional[List[str]] = None,
    tickers: Optional[List[str]] = None,
    dry_run: bool = False
) -> Dict:
    """
    Main entry point for SEC EDGAR ingestion.
    
    Args:
        session: Database session
        filing_types: Filing types to fetch
        tickers: Stock tickers to monitor
        dry_run: Skip database writes
    
    Returns:
        Summary dict with counts
    """
    print("[SEC_EDGAR][START] Beginning SEC EDGAR ingestion")
    
    filings = fetch_recent_filings(filing_types, tickers)
    print(f"[SEC_EDGAR][FETCH] Found {len(filings)} filings")
    
    macro_events = create_macro_events_from_filings(session, filings, dry_run)
    print(f"[SEC_EDGAR][MACRO_EVENTS] Created {len(macro_events)} macro events")
    
    result = {
        "filings_found": len(filings),
        "macro_events_created": len(macro_events),
        "dry_run": dry_run,
    }
    
    print(f"[SEC_EDGAR][COMPLETE] {result}")
    return result


print("[SEC_EDGAR][STARTUP] SEC EDGAR Connector loaded - EPIC 5")
