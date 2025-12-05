"""
OPERATION NAMESTORM: Enhanced Company Name Extraction Engine

Extracts high-confidence company names from signals using multiple sources:
1. Schema.org / JSON-LD structured data
2. OpenGraph and meta tag parsing
3. NER-like pattern matching for business names
4. Article headline and body heuristics

Each extraction method returns candidates with confidence scores.
The best candidate is selected based on weighted scoring.

Pure web scraping - NO paid NER APIs required.
"""

import os
import re
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Tuple
from datetime import datetime

import requests
from requests.exceptions import RequestException, Timeout

NAMESTORM_TIMEOUT = int(os.getenv("NAMESTORM_TIMEOUT", "8"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

BUSINESS_TERMS = {
    'air', 'hvac', 'ac', 'cooling', 'heating', 'plumbing', 'electric', 'electrical',
    'roofing', 'construction', 'remodeling', 'renovation', 'landscaping', 'lawn',
    'pool', 'spa', 'med spa', 'medspa', 'medical', 'clinic', 'dental', 'chiropractic',
    'realty', 'real estate', 'properties', 'property', 'homes', 'mortgage',
    'salon', 'nails', 'beauty', 'barber', 'hair', 'lashes', 'aesthetics',
    'law', 'legal', 'attorney', 'attorneys', 'lawyers', 'immigration', 'injury',
    'marketing', 'agency', 'media', 'studio', 'design', 'creative', 'digital',
    'consulting', 'consultants', 'advisors', 'advisory', 'partners', 'group',
    'solutions', 'services', 'systems', 'technologies', 'tech', 'software',
    'insurance', 'financial', 'accounting', 'tax', 'bookkeeping', 'cpa',
    'moving', 'storage', 'logistics', 'transport', 'trucking', 'shipping',
    'cleaning', 'janitorial', 'maid', 'housekeeping', 'restoration', 'remediation',
    'pest', 'termite', 'exterminating', 'wildlife', 'animal',
    'security', 'alarm', 'locksmith', 'doors', 'windows', 'glass',
    'flooring', 'carpet', 'tile', 'painting', 'drywall', 'insulation',
    'garage', 'doors', 'fencing', 'paving', 'concrete', 'masonry',
    'auto', 'automotive', 'collision', 'body shop', 'mechanic', 'tire',
    'restaurant', 'catering', 'food', 'bakery', 'cafe', 'coffee',
    'fitness', 'gym', 'yoga', 'pilates', 'personal training', 'crossfit',
    'daycare', 'childcare', 'preschool', 'tutoring', 'education',
    'pet', 'veterinary', 'vet', 'grooming', 'boarding', 'kennel',
    'photography', 'video', 'production', 'events', 'wedding', 'dj',
    'inc', 'llc', 'corp', 'corporation', 'co', 'company', 'enterprises',
    'associates', 'holdings', 'international', 'global', 'premier', 'elite',
    'professional', 'pro', 'express', 'rapid', 'quick', 'fast',
    'first', 'best', 'top', 'prime', 'superior', 'quality', 'choice',
    'american', 'national', 'united', 'coastal', 'southern', 'florida',
}

GENERIC_NAMES_BLOCK = {
    'the company', 'this company', 'the business', 'your company', 'our company',
    'developer', 'owner', 'manager', 'president', 'ceo', 'founder',
    'news', 'report', 'update', 'article', 'story', 'press', 'release',
    'south florida', 'miami', 'broward', 'palm beach', 'orlando', 'tampa',
    'florida', 'texas', 'california', 'new york', 'chicago',
    'local', 'area', 'region', 'county', 'city', 'state', 'national',
}

NEWS_OUTLET_NAMES = {
    'miami herald', 'sun sentinel', 'palm beach post', 'orlando sentinel',
    'tampa bay times', 'south florida business journal', 'bizjournals',
    'cbs miami', 'nbc miami', 'abc news', 'fox news', 'cnn', 'reuters',
    'associated press', 'bloomberg', 'wall street journal', 'new york times',
    'washington post', 'local10', 'wsvn', 'wplg', 'wfor', 'wtvj',
}


@dataclass
class CompanyCandidate:
    """A potential company name with confidence scoring."""
    name: str
    confidence: float
    source: str
    raw_match: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "confidence": self.confidence,
            "source": self.source,
            "raw_match": self.raw_match
        }


@dataclass
class NameStormResult:
    """Result of company name extraction."""
    success: bool
    best_candidate: Optional[CompanyCandidate] = None
    all_candidates: List[CompanyCandidate] = field(default_factory=list)
    source_url: Optional[str] = None
    extraction_time_ms: int = 0
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "best_candidate": self.best_candidate.to_dict() if self.best_candidate else None,
            "all_candidates": [c.to_dict() for c in self.all_candidates],
            "source_url": self.source_url,
            "extraction_time_ms": self.extraction_time_ms,
            "error": self.error
        }


def log_namestorm(action: str, lead_event_id: Optional[int] = None, details: Optional[Dict] = None) -> None:
    """Log NAMESTORM extraction activity."""
    msg_parts = [f"[NAMESTORM][{action.upper()}]"]
    if lead_event_id:
        msg_parts.append(f"event={lead_event_id}")
    if details:
        for k, v in details.items():
            if isinstance(v, str) and len(v) > 60:
                v = v[:60] + "..."
            msg_parts.append(f"{k}={v}")
    print(" | ".join(msg_parts))


def _normalize_company_name(name: str) -> str:
    """Clean and normalize company name."""
    if not name:
        return ""
    
    name = re.sub(r'\s+', ' ', name.strip())
    name = re.sub(r'[""''`]', '', name)
    name = re.sub(r'^(a|an|the)\s+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*[,;:]\s*$', '', name)
    name = re.sub(r'\s*\.{2,}\s*$', '', name)
    name = re.sub(r'\s+(announced|expands|opens|acquires|launches|hires|reports|says|to|will|has|is|are|was|were|bought|sold|filed|closes).*$', '', name, flags=re.IGNORECASE)
    
    return name.strip()


def _has_business_term(name: str) -> bool:
    """Check if name contains a business-related term."""
    name_lower = name.lower()
    for term in BUSINESS_TERMS:
        if term in name_lower:
            return True
    return False


def _is_blocked_name(name: str) -> bool:
    """Check if name should be blocked."""
    if not name:
        return True
    
    name_lower = name.lower().strip()
    
    if len(name_lower) < 3:
        return True
    
    if name_lower in GENERIC_NAMES_BLOCK:
        return True
    
    if name_lower in NEWS_OUTLET_NAMES:
        return True
    
    for blocked in GENERIC_NAMES_BLOCK:
        if name_lower == blocked:
            return True
    
    for outlet in NEWS_OUTLET_NAMES:
        if name_lower == outlet or outlet in name_lower:
            return True
    
    words = name_lower.split()
    if len(words) == 1 and words[0] not in BUSINESS_TERMS:
        if not words[0][0].isupper() if name else True:
            return True
    
    geo_only_pattern = r'^(miami|broward|palm beach|orlando|tampa|florida|south florida|texas|california)\s+(company|business|firm|group|owner)$'
    if re.match(geo_only_pattern, name_lower):
        return True
    
    generic_patterns = [
        r'^(owner|manager|president|ceo|founder)\s+of\s+',
        r'buys new', r'opens new', r'expands to', r'announces',
        r'^(local|area|regional)\s+(hvac|roofing|plumbing)',
    ]
    for pattern in generic_patterns:
        if re.search(pattern, name_lower):
            return True
    
    return False


def _calculate_confidence(name: str, source: str) -> float:
    """Calculate confidence score for a company name candidate."""
    base_confidence = {
        "schema_org": 0.95,
        "og_site_name": 0.85,
        "og_title": 0.75,
        "meta_title": 0.70,
        "h1_heading": 0.65,
        "ner_pattern": 0.60,
        "title_extraction": 0.55,
        "body_heuristic": 0.50,
        "summary_pattern": 0.45,
    }.get(source, 0.40)
    
    if _has_business_term(name):
        base_confidence += 0.10
    
    word_count = len(name.split())
    if 2 <= word_count <= 4:
        base_confidence += 0.05
    elif word_count > 6:
        base_confidence -= 0.10
    
    if name[0].isupper():
        base_confidence += 0.05
    
    if re.search(r'\b(Inc|LLC|Corp|Co)\b', name, re.IGNORECASE):
        base_confidence += 0.05
    
    return min(1.0, max(0.0, base_confidence))


def extract_from_schema_org(html: str) -> List[CompanyCandidate]:
    """Extract company names from Schema.org JSON-LD structured data."""
    candidates = []
    
    try:
        jsonld_pattern = re.compile(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            re.DOTALL | re.IGNORECASE
        )
        
        for match in jsonld_pattern.finditer(html):
            try:
                data = json.loads(match.group(1).strip())
                
                if isinstance(data, list):
                    for item in data:
                        candidates.extend(_process_schema_item(item))
                else:
                    candidates.extend(_process_schema_item(data))
                    
            except json.JSONDecodeError:
                continue
                
    except Exception:
        pass
    
    return candidates


def _process_schema_item(item: Dict) -> List[CompanyCandidate]:
    """Process a single schema.org item for company names."""
    candidates = []
    
    if not isinstance(item, dict):
        return candidates
    
    org_types = {'Organization', 'LocalBusiness', 'Corporation', 'LegalService',
                 'HomeAndConstructionBusiness', 'ProfessionalService', 'MedicalBusiness',
                 'FinancialService', 'RealEstateAgent', 'Store', 'Restaurant'}
    
    item_type = item.get('@type', '')
    if isinstance(item_type, list):
        item_type = item_type[0] if item_type else ''
    
    if item_type in org_types:
        name = item.get('name', '')
        if name and not _is_blocked_name(name):
            normalized = _normalize_company_name(name)
            if normalized and not _is_blocked_name(normalized):
                candidates.append(CompanyCandidate(
                    name=normalized,
                    confidence=_calculate_confidence(normalized, "schema_org"),
                    source="schema_org",
                    raw_match=name
                ))
    
    if '@graph' in item:
        for node in item['@graph']:
            candidates.extend(_process_schema_item(node))
    
    return candidates


def extract_from_meta_tags(html: str) -> List[CompanyCandidate]:
    """Extract company names from OpenGraph and meta tags."""
    candidates = []
    
    try:
        og_site_name = re.search(
            r'<meta[^>]*property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        if not og_site_name:
            og_site_name = re.search(
                r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:site_name["\']',
                html, re.IGNORECASE
            )
        
        if og_site_name:
            name = _normalize_company_name(og_site_name.group(1))
            if name and not _is_blocked_name(name):
                candidates.append(CompanyCandidate(
                    name=name,
                    confidence=_calculate_confidence(name, "og_site_name"),
                    source="og_site_name",
                    raw_match=og_site_name.group(1)
                ))
        
        og_title = re.search(
            r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        if og_title:
            title_text = og_title.group(1)
            parts = re.split(r'\s*[|\-–—]\s*', title_text)
            for part in parts:
                name = _normalize_company_name(part)
                if name and not _is_blocked_name(name) and _has_business_term(name):
                    candidates.append(CompanyCandidate(
                        name=name,
                        confidence=_calculate_confidence(name, "og_title"),
                        source="og_title",
                        raw_match=part
                    ))
        
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        if title_match:
            title_text = title_match.group(1)
            parts = re.split(r'\s*[|\-–—]\s*', title_text)
            for part in parts:
                name = _normalize_company_name(part)
                if name and not _is_blocked_name(name) and _has_business_term(name):
                    candidates.append(CompanyCandidate(
                        name=name,
                        confidence=_calculate_confidence(name, "meta_title"),
                        source="meta_title",
                        raw_match=part
                    ))
                    
    except Exception:
        pass
    
    return candidates


def extract_from_headings(html: str) -> List[CompanyCandidate]:
    """Extract company names from H1/H2 headings."""
    candidates = []
    
    try:
        h1_pattern = re.compile(r'<h1[^>]*>([^<]+)</h1>', re.IGNORECASE)
        for match in h1_pattern.finditer(html[:20000]):
            text = match.group(1).strip()
            name = _normalize_company_name(text)
            if name and not _is_blocked_name(name) and _has_business_term(name):
                candidates.append(CompanyCandidate(
                    name=name,
                    confidence=_calculate_confidence(name, "h1_heading"),
                    source="h1_heading",
                    raw_match=text
                ))
                
    except Exception:
        pass
    
    return candidates


def extract_from_text_patterns(text: str, source_type: str = "body_heuristic") -> List[CompanyCandidate]:
    """
    Extract company names using NER-like pattern matching.
    
    Patterns matched:
    - Proper Noun + Business Term (e.g., "Miami Best Roofing")
    - Business Name + Legal Suffix (e.g., "Smith & Sons LLC")
    - Quote-enclosed names (e.g., '"Acme Corp" announced...')
    """
    candidates = []
    
    if not text:
        return candidates
    
    try:
        business_term_pattern = '|'.join(re.escape(t) for t in sorted(BUSINESS_TERMS, key=len, reverse=True))
        
        patterns = [
            rf'([A-Z][a-zA-Z]+(?:\s+[A-Z]?[a-zA-Z&\'\-]+){{0,4}}\s+(?:{business_term_pattern}))',
            
            r'([A-Z][a-zA-Z]+(?:\s+[A-Z]?[a-zA-Z&\'\-]+){0,3}\s+(?:Inc|LLC|Corp|Co|Ltd|LLP|PLLC|PC|PA)\.?)',
            
            r'"([A-Z][^"]{3,50})"',
            
            r'([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}),\s+(?:a|an|the)\s+(?:Miami|Florida|local|leading)',
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                raw_name = match.group(1) if match.lastindex else match.group(0)
                name = _normalize_company_name(raw_name)
                
                if name and not _is_blocked_name(name) and len(name) >= 3:
                    if _has_business_term(name) or re.search(r'\b(Inc|LLC|Corp)\b', name, re.IGNORECASE):
                        candidates.append(CompanyCandidate(
                            name=name,
                            confidence=_calculate_confidence(name, source_type),
                            source=source_type,
                            raw_match=raw_name
                        ))
                        
    except Exception:
        pass
    
    return candidates


def extract_company_candidates(
    title: Optional[str] = None,
    summary: Optional[str] = None,
    source_url: Optional[str] = None,
    lead_event_id: Optional[int] = None,
    fetch_page: bool = True
) -> NameStormResult:
    """
    NAMESTORM: Extract company name candidates from signal context.
    
    Uses multiple extraction methods in priority order:
    1. Schema.org JSON-LD from article page
    2. OpenGraph meta tags
    3. Page title parsing
    4. H1 heading extraction
    5. NER-like patterns from title/summary
    6. Heuristic patterns from article body
    
    Args:
        title: Signal title or headline
        summary: Signal summary/description
        source_url: URL of the signal source
        lead_event_id: Optional ID for logging
        fetch_page: Whether to fetch the source URL for extraction
        
    Returns:
        NameStormResult with sorted candidates (highest confidence first)
    """
    import time
    start_time = time.time()
    
    all_candidates: List[CompanyCandidate] = []
    
    log_namestorm("START", lead_event_id, {
        "has_title": bool(title),
        "has_summary": bool(summary),
        "has_url": bool(source_url)
    })
    
    if title:
        title_candidates = extract_from_text_patterns(title, "title_extraction")
        all_candidates.extend(title_candidates)
    
    if summary:
        summary_candidates = extract_from_text_patterns(summary, "summary_pattern")
        all_candidates.extend(summary_candidates)
    
    if fetch_page and source_url and 'news.google.com' not in source_url:
        try:
            headers = {"User-Agent": USER_AGENTS[0]}
            response = requests.get(source_url, headers=headers, timeout=NAMESTORM_TIMEOUT)
            
            if response.status_code == 200:
                html = response.text[:100000]
                
                schema_candidates = extract_from_schema_org(html)
                all_candidates.extend(schema_candidates)
                
                meta_candidates = extract_from_meta_tags(html)
                all_candidates.extend(meta_candidates)
                
                heading_candidates = extract_from_headings(html)
                all_candidates.extend(heading_candidates)
                
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, 'html.parser')
                    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                        tag.decompose()
                    
                    paragraphs = soup.find_all('p')[:10]
                    body_text = ' '.join(p.get_text(strip=True) for p in paragraphs)
                    
                    if body_text:
                        body_candidates = extract_from_text_patterns(body_text, "body_heuristic")
                        all_candidates.extend(body_candidates)
                except ImportError:
                    pass
                    
        except (RequestException, Timeout) as e:
            log_namestorm("FETCH_ERROR", lead_event_id, {"error": str(e)[:50]})
    
    seen_names: Set[str] = set()
    unique_candidates: List[CompanyCandidate] = []
    
    for candidate in all_candidates:
        name_key = candidate.name.lower().strip()
        if name_key not in seen_names:
            seen_names.add(name_key)
            unique_candidates.append(candidate)
    
    unique_candidates.sort(key=lambda c: c.confidence, reverse=True)
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    if unique_candidates:
        best = unique_candidates[0]
        log_namestorm("CANDIDATES", lead_event_id, {
            "count": len(unique_candidates),
            "best": best.name,
            "confidence": f"{best.confidence:.2f}",
            "source": best.source
        })
        
        return NameStormResult(
            success=True,
            best_candidate=best,
            all_candidates=unique_candidates[:10],
            source_url=source_url,
            extraction_time_ms=elapsed_ms
        )
    else:
        log_namestorm("NO_CANDIDATES", lead_event_id, {
            "title": title[:40] if title else None,
            "summary": summary[:40] if summary else None
        })
        
        return NameStormResult(
            success=False,
            source_url=source_url,
            extraction_time_ms=elapsed_ms,
            error="No valid company candidates found"
        )


def get_best_company_name(
    title: Optional[str] = None,
    summary: Optional[str] = None,
    source_url: Optional[str] = None,
    lead_event_id: Optional[int] = None,
    min_confidence: float = 0.5
) -> Optional[str]:
    """
    Convenience function to get the best company name or None.
    
    Returns the highest-confidence company name if it meets the threshold.
    """
    result = extract_company_candidates(
        title=title,
        summary=summary,
        source_url=source_url,
        lead_event_id=lead_event_id,
        fetch_page=True
    )
    
    if result.success and result.best_candidate:
        if result.best_candidate.confidence >= min_confidence:
            return result.best_candidate.name
    
    return None
