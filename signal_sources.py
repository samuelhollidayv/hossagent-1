"""
SignalNet Framework for HossAgent

A modular signal ingestion system that transforms HossAgent from generic lead gen
into a context-aware intelligence engine. Signal sources detect timing/context
signals, not contact data - Apollo remains the only lead source.

============================================================================
SIGNAL_MODE CONFIGURATION
============================================================================
Environment variable SIGNAL_MODE controls signal pipeline behavior:

  PRODUCTION: Run real sources, create LeadEvents for high-scoring signals
  SANDBOX: Run sources and score signals, but don't create LeadEvents
  OFF: Skip signal ingestion entirely

Default: SANDBOX (safe mode for development)

============================================================================
ARCHITECTURE
============================================================================

  SignalSource (ABC)         - Abstract base class for signal sources
       |
  SignalRegistry             - Manages and registers sources, handles cooldowns
       |
  SignalPipeline            - Orchestrates fetch -> parse -> score -> persist
       |
  score_signal()            - Scoring utility with weighted factors

============================================================================
MIAMI-FIRST TARGETING
============================================================================
Via LEAD_GEOGRAPHY and LEAD_NICHE environment variables:
  - Geography match: +15 score boost
  - Niche match: +10 score boost
  - Miami-tuned urgency categories (HURRICANE=95, etc.)
============================================================================
"""

import json
import os
import requests
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Type
from sqlmodel import Session, select

from models import Signal, LeadEvent


OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")


SIGNAL_MODE = os.environ.get("SIGNAL_MODE", "SANDBOX").upper()
LEAD_GEOGRAPHY = os.environ.get("LEAD_GEOGRAPHY", "Miami, Broward, South Florida")
LEAD_NICHE = os.environ.get("LEAD_NICHE", "HVAC, Roofing, Med Spa, Immigration Attorney")

LEAD_GEOGRAPHY_LIST = [g.strip().lower() for g in LEAD_GEOGRAPHY.split(",")]
LEAD_NICHE_LIST = [n.strip().lower() for n in LEAD_NICHE.split(",")]

LEADEVENT_SCORE_THRESHOLD = 65

URGENCY_CATEGORY_WEIGHTS = {
    "HURRICANE": 95,
    "HURRICANE_SEASON": 95,
    "GROWTH_SIGNAL": 80,
    "REVIEW": 70,
    "REPUTATION_CHANGE": 70,
    "COMPETITOR_SHIFT": 75,
    "MIAMI_PRICE_MOVE": 70,
    "BILINGUAL_OPPORTUNITY": 65,
    "NEWS": 60,
    "PERMIT": 55,
    "JOB_POSTING": 55,
    "OPPORTUNITY": 50,
    "DEFAULT": 50,
}

print(f"[SIGNALNET][STARTUP] Mode: {SIGNAL_MODE}, Geography: {LEAD_GEOGRAPHY}, Niche: {LEAD_NICHE}")


@dataclass
class RawSignal:
    """
    Raw signal data fetched from a source before parsing.
    
    This intermediate representation allows sources to return
    unprocessed data that gets standardized during parsing.
    """
    source_name: str
    source_type: str
    raw_data: Dict[str, Any]
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    geography: Optional[str] = None
    company_hint: Optional[str] = None
    lead_id_hint: Optional[int] = None
    company_id_hint: Optional[int] = None


@dataclass
class ParsedSignal:
    """
    Parsed signal ready for scoring and persistence.
    
    Represents a standardized signal after source-specific parsing.
    """
    source_type: str
    raw_payload: str
    context_summary: str
    geography: Optional[str] = None
    lead_id: Optional[int] = None
    company_id: Optional[int] = None
    category_hint: Optional[str] = None
    niche_hint: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ScoredSignal:
    """
    Signal with computed score and explanation.
    """
    parsed_signal: ParsedSignal
    score: int
    score_explanation: str
    should_create_event: bool


class SignalSource(ABC):
    """
    Abstract base class for signal sources.
    
    Each signal source represents a data feed that provides context/timing
    information about companies and markets. Signal sources do NOT provide
    contact data - Apollo is the only lead source.
    
    Subclasses must implement:
      - fetch() -> List[RawSignal]: Get raw signals from the source
      - parse(raw: RawSignal) -> ParsedSignal: Convert raw to parsed format
    
    Source lifecycle is tracked via:
      - last_run: When the source was last executed
      - last_error: Most recent error message (if any)
      - items_last_run: Number of signals fetched in last run
    
    Cooldown and rate limiting:
      - cooldown_seconds: Minimum time between runs
      - max_items_per_run: Cap on signals per execution
    """
    
    def __init__(self):
        self._last_run: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._items_last_run: int = 0
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this source (e.g., 'google_reviews', 'indeed_jobs')."""
        ...
    
    @property
    @abstractmethod
    def source_type(self) -> str:
        """Category of signals this source provides (e.g., 'review', 'job_posting')."""
        ...
    
    @property
    def enabled(self) -> bool:
        """Whether this source is active. Override to add conditional logic."""
        return True
    
    @property
    def cooldown_seconds(self) -> int:
        """Minimum seconds between runs. Override for source-specific cooldowns."""
        return 300
    
    @property
    def max_items_per_run(self) -> int:
        """Maximum signals to fetch per run. Override for rate limiting."""
        return 50
    
    @property
    def last_run(self) -> Optional[datetime]:
        """When this source was last executed."""
        return self._last_run
    
    @property
    def last_error(self) -> Optional[str]:
        """Most recent error message, if any."""
        return self._last_error
    
    @property
    def items_last_run(self) -> int:
        """Number of signals fetched in last run."""
        return self._items_last_run
    
    def is_eligible(self) -> bool:
        """
        Check if this source is eligible to run.
        
        Returns True if:
          - Source is enabled
          - Cooldown period has elapsed since last_run
        """
        if not self.enabled:
            return False
        
        if self._last_run is None:
            return True
        
        elapsed = (datetime.utcnow() - self._last_run).total_seconds()
        return elapsed >= self.cooldown_seconds
    
    def record_run(self, items_count: int, error: Optional[str] = None):
        """Record the results of a run."""
        self._last_run = datetime.utcnow()
        self._items_last_run = items_count
        self._last_error = error
    
    @abstractmethod
    def fetch(self) -> List[RawSignal]:
        """
        Fetch raw signals from the source.
        
        Returns:
            List of RawSignal objects containing unprocessed source data.
            
        Raises:
            Exception: On fetch failure (will be captured by pipeline)
        """
        ...
    
    @abstractmethod
    def parse(self, raw: RawSignal) -> ParsedSignal:
        """
        Parse a raw signal into standardized format.
        
        Args:
            raw: RawSignal from fetch()
            
        Returns:
            ParsedSignal ready for scoring and persistence
        """
        ...
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status of this source."""
        return {
            "name": self.name,
            "source_type": self.source_type,
            "enabled": self.enabled,
            "cooldown_seconds": self.cooldown_seconds,
            "max_items_per_run": self.max_items_per_run,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_error": self._last_error,
            "items_last_run": self._items_last_run,
            "is_eligible": self.is_eligible(),
        }


class SignalRegistry:
    """
    Registry for managing SignalSource instances.
    
    Handles:
      - Registration of source classes and instances
      - Eligibility checking based on cooldowns and enabled flags
      - Retrieval of sources for pipeline execution
    """
    
    def __init__(self):
        self._sources: Dict[str, SignalSource] = {}
        self._source_classes: Dict[str, Type[SignalSource]] = {}
    
    def register_class(self, source_class: Type[SignalSource]) -> None:
        """
        Register a SignalSource class for lazy instantiation.
        
        Args:
            source_class: A SignalSource subclass (not an instance)
        """
        temp_instance = source_class()
        self._source_classes[temp_instance.name] = source_class
        print(f"[SIGNALNET][REGISTRY] Registered source class: {temp_instance.name}")
    
    def register(self, source: SignalSource) -> None:
        """
        Register an instantiated SignalSource.
        
        Args:
            source: A SignalSource instance
        """
        self._sources[source.name] = source
        print(f"[SIGNALNET][REGISTRY] Registered source: {source.name} ({source.source_type})")
    
    def unregister(self, name: str) -> bool:
        """
        Remove a source from the registry.
        
        Args:
            name: Name of the source to remove
            
        Returns:
            True if removed, False if not found
        """
        if name in self._sources:
            del self._sources[name]
            print(f"[SIGNALNET][REGISTRY] Unregistered source: {name}")
            return True
        if name in self._source_classes:
            del self._source_classes[name]
            return True
        return False
    
    def get_source(self, name: str) -> Optional[SignalSource]:
        """Get a specific source by name."""
        if name in self._sources:
            return self._sources[name]
        if name in self._source_classes:
            self._sources[name] = self._source_classes[name]()
            return self._sources[name]
        return None
    
    def get_all_sources(self) -> List[SignalSource]:
        """Get all registered sources (instantiated)."""
        for name, cls in self._source_classes.items():
            if name not in self._sources:
                self._sources[name] = cls()
        return list(self._sources.values())
    
    def get_eligible_sources(self) -> List[SignalSource]:
        """
        Get sources eligible to run in the current cycle.
        
        Returns sources that:
          - Are enabled
          - Have passed their cooldown period
        """
        all_sources = self.get_all_sources()
        eligible = [s for s in all_sources if s.is_eligible()]
        
        print(f"[SIGNALNET][REGISTRY] {len(eligible)}/{len(all_sources)} sources eligible")
        return eligible
    
    def get_status(self) -> Dict[str, Any]:
        """Get status of all registered sources."""
        all_sources = self.get_all_sources()
        return {
            "total_sources": len(all_sources),
            "eligible_sources": len([s for s in all_sources if s.is_eligible()]),
            "sources": [s.get_status() for s in all_sources],
        }


def _matches_lead_geography(geography: Optional[str]) -> bool:
    """Check if geography matches configured LEAD_GEOGRAPHY."""
    if not geography:
        return False
    geo_lower = geography.lower()
    return any(target in geo_lower for target in LEAD_GEOGRAPHY_LIST)


def _matches_lead_niche(niche: Optional[str]) -> bool:
    """Check if niche matches configured LEAD_NICHE."""
    if not niche:
        return False
    niche_lower = niche.lower()
    return any(target in niche_lower for target in LEAD_NICHE_LIST)


def _calculate_recency_score(created_at: datetime, max_age_hours: int = 72) -> int:
    """
    Calculate recency score (0-100) based on signal age.
    
    Newer signals score higher:
      - 0-6 hours: 100
      - 6-24 hours: 80-99
      - 24-48 hours: 60-79
      - 48-72 hours: 40-59
      - 72+ hours: 20-39
    """
    now = datetime.utcnow()
    age_hours = (now - created_at).total_seconds() / 3600
    
    if age_hours <= 6:
        return 100
    elif age_hours <= 24:
        return int(80 + (24 - age_hours) / 18 * 19)
    elif age_hours <= 48:
        return int(60 + (48 - age_hours) / 24 * 19)
    elif age_hours <= max_age_hours:
        return int(40 + (max_age_hours - age_hours) / 24 * 19)
    else:
        return max(20, int(40 - (age_hours - max_age_hours) / 24 * 10))


def score_signal(
    parsed_signal: ParsedSignal,
    category: Optional[str] = None,
) -> ScoredSignal:
    """
    Score a parsed signal based on weighted factors.
    
    Scoring components (0-100 final range):
      1. Urgency category weight (30% of score)
         - HURRICANE: 95 base
         - GROWTH_SIGNAL: 80 base
         - REVIEW: 70 base
         - etc.
      
      2. Recency decay (25% of score)
         - Newer signals score higher
         - Decays over 72 hours
      
      3. Geography match boost (25% of score)
         - +25 if matches LEAD_GEOGRAPHY
         - 0 otherwise
      
      4. Niche match boost (20% of score)
         - +20 if matches LEAD_NICHE
         - 0 otherwise
    
    Args:
        parsed_signal: The ParsedSignal to score
        category: Optional category override (inferred if not provided)
        
    Returns:
        ScoredSignal with score, explanation, and event creation flag
    """
    explanation_parts = []
    
    if category is None:
        category = parsed_signal.category_hint or _infer_category(
            parsed_signal.source_type,
            parsed_signal.context_summary
        )
    
    category_upper = category.upper()
    category_base = URGENCY_CATEGORY_WEIGHTS.get(
        category_upper,
        URGENCY_CATEGORY_WEIGHTS["DEFAULT"]
    )
    category_score = int(category_base * 0.30)
    explanation_parts.append(f"Category {category}: {category_base}×0.30 = {category_score}")
    
    recency_base = _calculate_recency_score(parsed_signal.created_at)
    recency_score = int(recency_base * 0.25)
    age_hours = (datetime.utcnow() - parsed_signal.created_at).total_seconds() / 3600
    explanation_parts.append(f"Recency ({age_hours:.1f}h old): {recency_base}×0.25 = {recency_score}")
    
    geo_match = _matches_lead_geography(parsed_signal.geography)
    geo_score = 25 if geo_match else 0
    geo_status = f"MATCH ({parsed_signal.geography})" if geo_match else f"no match ({parsed_signal.geography or 'none'})"
    explanation_parts.append(f"Geography {geo_status}: {geo_score}")
    
    niche_match = _matches_lead_niche(parsed_signal.niche_hint)
    niche_score = 20 if niche_match else 0
    niche_status = f"MATCH ({parsed_signal.niche_hint})" if niche_match else f"no match ({parsed_signal.niche_hint or 'none'})"
    explanation_parts.append(f"Niche {niche_status}: {niche_score}")
    
    total_score = category_score + recency_score + geo_score + niche_score
    total_score = max(0, min(100, total_score))
    
    should_create_event = total_score >= LEADEVENT_SCORE_THRESHOLD
    
    explanation = f"Total: {total_score}/100 | " + " | ".join(explanation_parts)
    
    return ScoredSignal(
        parsed_signal=parsed_signal,
        score=total_score,
        score_explanation=explanation,
        should_create_event=should_create_event,
    )


def _infer_category(source_type: str, context: str) -> str:
    """
    Infer signal category from source type and context.
    
    Miami-tuned categories:
      - HURRICANE_SEASON: Storm/hurricane signals
      - BILINGUAL_OPPORTUNITY: Spanish/bilingual signals
      - MIAMI_PRICE_MOVE: Local pricing changes
      - COMPETITOR_SHIFT: Competitor positioning
      - GROWTH_SIGNAL: Hiring/expansion
      - REPUTATION_CHANGE: Review signals
      - OPPORTUNITY: General opportunity
    """
    context_lower = context.lower()
    
    if "hurricane" in context_lower or "storm" in context_lower:
        return "HURRICANE_SEASON"
    elif "bilingual" in context_lower or "spanish" in context_lower:
        return "BILINGUAL_OPPORTUNITY"
    elif "price" in context_lower and ("miami" in context_lower or "local" in context_lower):
        return "MIAMI_PRICE_MOVE"
    elif "competitor" in context_lower or "pricing" in context_lower:
        return "COMPETITOR_SHIFT"
    elif "hiring" in context_lower or "job" in context_lower or "growth" in context_lower:
        return "GROWTH_SIGNAL"
    elif "review" in context_lower:
        return "REPUTATION_CHANGE"
    elif source_type == "job_posting":
        return "GROWTH_SIGNAL"
    elif source_type == "review":
        return "REPUTATION_CHANGE"
    elif source_type == "competitor_update":
        return "COMPETITOR_SHIFT"
    elif source_type == "weather":
        return "HURRICANE_SEASON"
    elif source_type == "permit":
        return "GROWTH_SIGNAL"
    else:
        return "OPPORTUNITY"


def _generate_recommended_action(category: str, context: str) -> str:
    """Generate recommended action based on category."""
    actions = {
        "HURRICANE_SEASON": "Offer hurricane-season discount bundle or preparedness package",
        "COMPETITOR_SHIFT": "Send competitive analysis snapshot highlighting your differentiators",
        "GROWTH_SIGNAL": "Propose partnership or capacity-building services",
        "BILINGUAL_OPPORTUNITY": "Highlight bilingual staff on homepage - big ROI in Miami market",
        "REPUTATION_CHANGE": "Offer reputation management or customer experience audit",
        "MIAMI_PRICE_MOVE": "Prepare market pricing comparison and value proposition",
        "OPPORTUNITY": "Send contextual outreach with relevant service offer",
    }
    return actions.get(category.upper(), "Prepare contextual outreach based on signal")


class SignalPipeline:
    """
    Orchestrates the signal ingestion pipeline.
    
    Pipeline stages:
      1. Get eligible sources from registry
      2. Fetch raw signals from each source
      3. Parse raw signals into standardized format
      4. Score each signal
      5. Persist signals to database
      6. Generate LeadEvents for signals scoring >= 65 (PRODUCTION mode only)
    
    Mode behavior (via SIGNAL_MODE env var):
      - PRODUCTION: Full pipeline including LeadEvent creation
      - SANDBOX: Fetch, parse, score, persist signals - skip LeadEvent creation
      - OFF: Skip signal ingestion entirely
    """
    
    def __init__(self, registry: SignalRegistry, session: Session):
        self.registry = registry
        self.session = session
        self.mode = SIGNAL_MODE
    
    def run(self) -> Dict[str, Any]:
        """
        Execute the signal pipeline.
        
        Returns:
            Dict with pipeline execution results
        """
        if self.mode == "OFF":
            print("[SIGNALNET][PIPELINE] Mode is OFF - skipping signal ingestion")
            return {
                "mode": "OFF",
                "skipped": True,
                "signals_fetched": 0,
                "signals_persisted": 0,
                "events_created": 0,
            }
        
        print(f"[SIGNALNET][PIPELINE] Starting pipeline in {self.mode} mode...")
        
        eligible_sources = self.registry.get_eligible_sources()
        
        if not eligible_sources:
            print("[SIGNALNET][PIPELINE] No eligible sources - all on cooldown or disabled")
            return {
                "mode": self.mode,
                "skipped": False,
                "sources_checked": len(self.registry.get_all_sources()),
                "sources_eligible": 0,
                "signals_fetched": 0,
                "signals_persisted": 0,
                "events_created": 0,
            }
        
        results = {
            "mode": self.mode,
            "skipped": False,
            "sources_checked": len(self.registry.get_all_sources()),
            "sources_eligible": len(eligible_sources),
            "sources_run": [],
            "signals_fetched": 0,
            "signals_parsed": 0,
            "signals_scored": 0,
            "signals_persisted": 0,
            "events_created": 0,
            "errors": [],
        }
        
        for source in eligible_sources:
            source_result = self._run_source(source)
            results["sources_run"].append(source_result)
            results["signals_fetched"] += source_result.get("fetched", 0)
            results["signals_parsed"] += source_result.get("parsed", 0)
            results["signals_scored"] += source_result.get("scored", 0)
            results["signals_persisted"] += source_result.get("persisted", 0)
            results["events_created"] += source_result.get("events_created", 0)
            if source_result.get("error"):
                results["errors"].append({
                    "source": source.name,
                    "error": source_result["error"],
                })
        
        print(f"[SIGNALNET][PIPELINE] Complete: {results['signals_persisted']} signals, "
              f"{results['events_created']} events")
        
        return results
    
    def _run_source(self, source: SignalSource) -> Dict[str, Any]:
        """Run a single source through the pipeline."""
        result = {
            "source": source.name,
            "source_type": source.source_type,
            "fetched": 0,
            "parsed": 0,
            "scored": 0,
            "persisted": 0,
            "events_created": 0,
            "error": None,
        }
        
        try:
            print(f"[SIGNALNET][{source.name.upper()}] Fetching signals...")
            raw_signals = source.fetch()
            result["fetched"] = len(raw_signals)
            
            if len(raw_signals) > source.max_items_per_run:
                raw_signals = raw_signals[:source.max_items_per_run]
                print(f"[SIGNALNET][{source.name.upper()}] Capped at {source.max_items_per_run} items")
            
            for raw_signal in raw_signals:
                try:
                    parsed = source.parse(raw_signal)
                    result["parsed"] += 1
                    
                    scored = score_signal(parsed)
                    result["scored"] += 1
                    
                    signal = self._persist_signal(parsed)
                    result["persisted"] += 1
                    
                    if scored.should_create_event and self.mode == "PRODUCTION":
                        self._create_lead_event(signal, scored)
                        result["events_created"] += 1
                    elif scored.should_create_event and self.mode == "SANDBOX":
                        print(f"[SIGNALNET][SANDBOX] Would create event (score={scored.score}) - skipped in SANDBOX")
                    
                except Exception as parse_err:
                    print(f"[SIGNALNET][{source.name.upper()}] Parse error: {parse_err}")
            
            source.record_run(result["fetched"])
            
        except Exception as fetch_err:
            error_msg = str(fetch_err)
            result["error"] = error_msg
            source.record_run(0, error=error_msg)
            print(f"[SIGNALNET][{source.name.upper()}] Fetch error: {error_msg}")
        
        return result
    
    def _persist_signal(self, parsed: ParsedSignal) -> Signal:
        """Persist a parsed signal to the database."""
        signal = Signal(
            company_id=parsed.company_id,
            lead_id=parsed.lead_id,
            source_type=parsed.source_type,
            raw_payload=parsed.raw_payload,
            context_summary=parsed.context_summary,
            geography=parsed.geography,
        )
        self.session.add(signal)
        self.session.commit()
        self.session.refresh(signal)
        
        print(f"[SIGNALNET][PERSIST] Signal #{signal.id}: {parsed.source_type} - {parsed.context_summary[:60]}...")
        return signal
    
    def _create_lead_event(self, signal: Signal, scored: ScoredSignal) -> LeadEvent:
        """Create a LeadEvent from a high-scoring signal."""
        parsed = scored.parsed_signal
        category = parsed.category_hint or _infer_category(
            parsed.source_type,
            parsed.context_summary
        )
        
        recommended_action = _generate_recommended_action(category, parsed.context_summary)
        
        event = LeadEvent(
            company_id=signal.company_id,
            lead_id=signal.lead_id,
            signal_id=signal.id,
            summary=parsed.context_summary,
            category=category,
            urgency_score=scored.score,
            status="NEW",
            recommended_action=recommended_action,
        )
        self.session.add(event)
        self.session.commit()
        self.session.refresh(event)
        
        print(f"[SIGNALNET][EVENT] LeadEvent #{event.id}: {category} (urgency={scored.score})")
        return event


_global_registry = SignalRegistry()


def get_registry() -> SignalRegistry:
    """Get the global signal registry."""
    return _global_registry


def register_source(source: SignalSource) -> None:
    """Register a source with the global registry."""
    _global_registry.register(source)


def register_source_class(source_class: Type[SignalSource]) -> None:
    """Register a source class with the global registry."""
    _global_registry.register_class(source_class)


def run_signal_pipeline(session: Session) -> Dict[str, Any]:
    """
    Run the signal pipeline with the global registry.
    
    Args:
        session: SQLModel database session
        
    Returns:
        Dict with pipeline execution results
    """
    pipeline = SignalPipeline(_global_registry, session)
    return pipeline.run()


def get_signal_mode() -> str:
    """Get current SIGNAL_MODE setting."""
    return SIGNAL_MODE


def get_signal_status() -> Dict[str, Any]:
    """Get comprehensive status of the SignalNet system."""
    return {
        "mode": SIGNAL_MODE,
        "lead_geography": LEAD_GEOGRAPHY,
        "lead_niche": LEAD_NICHE,
        "leadevent_threshold": LEADEVENT_SCORE_THRESHOLD,
        "registry": _global_registry.get_status(),
    }


class SyntheticSignalSource(SignalSource):
    """
    Example synthetic signal source for development/testing.
    
    Generates synthetic signals based on the existing signals_agent patterns.
    Useful for testing the pipeline without real API integrations.
    """
    
    @property
    def name(self) -> str:
        return "synthetic_demo"
    
    @property
    def source_type(self) -> str:
        return "synthetic"
    
    @property
    def enabled(self) -> bool:
        return SIGNAL_MODE in ("SANDBOX", "PRODUCTION")
    
    @property
    def cooldown_seconds(self) -> int:
        return 60
    
    @property
    def max_items_per_run(self) -> int:
        return 10
    
    def fetch(self) -> List[RawSignal]:
        """Generate synthetic signals for testing."""
        import random
        
        miami_areas = [
            "Miami", "Coral Gables", "Brickell", "Wynwood", "Little Havana",
            "Doral", "Hialeah", "Miami Beach", "Fort Lauderdale", "Broward County",
        ]
        
        signal_templates = [
            {
                "type": "competitor_update",
                "summary": "Competitor updated pricing for core services",
                "category": "COMPETITOR_SHIFT",
            },
            {
                "type": "job_posting",
                "summary": "Hiring bilingual customer service representative",
                "category": "GROWTH_SIGNAL",
            },
            {
                "type": "review",
                "summary": "New 5-star review praising fast turnaround",
                "category": "REPUTATION_CHANGE",
            },
            {
                "type": "weather",
                "summary": "Hurricane preparedness advisory - increased demand expected",
                "category": "HURRICANE_SEASON",
            },
            {
                "type": "permit",
                "summary": "New building permit approved in target area",
                "category": "GROWTH_SIGNAL",
            },
        ]
        
        num_signals = random.randint(1, 5)
        signals = []
        
        for _ in range(num_signals):
            template = random.choice(signal_templates)
            area = random.choice(miami_areas)
            
            signals.append(RawSignal(
                source_name=self.name,
                source_type=template["type"],
                raw_data={
                    "summary": template["summary"],
                    "category": template["category"],
                    "area": area,
                    "timestamp": datetime.utcnow().isoformat(),
                },
                geography=area,
            ))
        
        return signals
    
    def parse(self, raw: RawSignal) -> ParsedSignal:
        """Parse synthetic signal."""
        return ParsedSignal(
            source_type=raw.source_type,
            raw_payload=json.dumps(raw.raw_data),
            context_summary=raw.raw_data.get("summary", "Synthetic signal"),
            geography=raw.geography,
            category_hint=raw.raw_data.get("category"),
            niche_hint=LEAD_NICHE.split(",")[0].strip() if LEAD_NICHE else None,
        )


class WeatherSignalSource(SignalSource):
    """
    Weather alerts signal source for South Florida.
    
    Uses OpenWeatherMap API (free tier) to detect:
    - Hurricanes and tropical storms
    - Extreme heat (>95F) driving HVAC demand
    - Cold fronts driving heating demand  
    - Heavy rain/storms driving roofing/water damage demand
    
    API Key: OPENWEATHER_API_KEY environment variable (optional)
    If no API key, source is disabled and logs a warning.
    """
    
    SOUTH_FLORIDA_LOCATIONS = [
        {"name": "Miami", "lat": 25.7617, "lon": -80.1918},
        {"name": "Fort Lauderdale", "lat": 26.1224, "lon": -80.1373},
        {"name": "Palm Beach", "lat": 26.7056, "lon": -80.0364},
    ]
    
    HEAT_THRESHOLD_F = 95
    COLD_THRESHOLD_F = 50
    HEAVY_RAIN_THRESHOLD_MM = 25
    
    STORM_KEYWORDS = [
        "hurricane", "tropical storm", "tropical depression", 
        "thunderstorm", "severe", "flood", "warning", "watch"
    ]
    
    @property
    def name(self) -> str:
        return "weather_openweather"
    
    @property
    def source_type(self) -> str:
        return "weather"
    
    @property
    def enabled(self) -> bool:
        if not OPENWEATHER_API_KEY:
            print("[SIGNALNET][WEATHER] No OPENWEATHER_API_KEY set - source disabled")
            return False
        return SIGNAL_MODE in ("SANDBOX", "PRODUCTION")
    
    @property
    def cooldown_seconds(self) -> int:
        return 3600
    
    @property
    def max_items_per_run(self) -> int:
        return 15
    
    def fetch(self) -> List[RawSignal]:
        """Fetch weather data from OpenWeatherMap for South Florida locations."""
        if not OPENWEATHER_API_KEY:
            print("[SIGNALNET][WEATHER] Skipping fetch - no API key")
            return []
        
        signals = []
        
        for location in self.SOUTH_FLORIDA_LOCATIONS:
            try:
                current_data = self._fetch_current_weather(location)
                if current_data:
                    signals.extend(self._analyze_current_weather(current_data, location))
                
                alerts_data = self._fetch_weather_alerts(location)
                if alerts_data:
                    signals.extend(self._analyze_weather_alerts(alerts_data, location))
                    
                time.sleep(0.25)
                
            except Exception as e:
                print(f"[SIGNALNET][WEATHER] Error fetching {location['name']}: {e}")
                continue
        
        print(f"[SIGNALNET][WEATHER] Fetched {len(signals)} weather signals from {len(self.SOUTH_FLORIDA_LOCATIONS)} locations")
        return signals
    
    def _fetch_current_weather(self, location: Dict) -> Optional[Dict]:
        """Fetch current weather for a location."""
        try:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "lat": location["lat"],
                "lon": location["lon"],
                "appid": OPENWEATHER_API_KEY,
                "units": "imperial"
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[SIGNALNET][WEATHER] Current weather API error: {e}")
            return None
    
    def _fetch_weather_alerts(self, location: Dict) -> Optional[Dict]:
        """Fetch weather alerts using One Call API (if available)."""
        try:
            url = "https://api.openweathermap.org/data/2.5/onecall"
            params = {
                "lat": location["lat"],
                "lon": location["lon"],
                "appid": OPENWEATHER_API_KEY,
                "exclude": "minutely,hourly,daily",
                "units": "imperial"
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 401:
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            return None
    
    def _analyze_current_weather(self, data: Dict, location: Dict) -> List[RawSignal]:
        """Analyze current weather for business-relevant signals."""
        signals = []
        
        main = data.get("main", {})
        weather = data.get("weather", [{}])[0]
        rain = data.get("rain", {})
        
        temp_f = main.get("temp", 70)
        feels_like_f = main.get("feels_like", 70)
        humidity = main.get("humidity", 50)
        description = weather.get("description", "").lower()
        weather_main = weather.get("main", "").lower()
        
        if temp_f >= self.HEAT_THRESHOLD_F or feels_like_f >= self.HEAT_THRESHOLD_F:
            signals.append(RawSignal(
                source_name=self.name,
                source_type="weather",
                raw_data={
                    "event_type": "extreme_heat",
                    "temp_f": temp_f,
                    "feels_like_f": feels_like_f,
                    "humidity": humidity,
                    "location": location["name"],
                    "description": f"Extreme heat alert: {temp_f}°F (feels like {feels_like_f}°F)",
                    "business_impact": "HVAC demand surge expected",
                    "niche_opportunities": ["HVAC", "pool service", "landscaping"],
                },
                geography=location["name"],
            ))
        
        if temp_f <= self.COLD_THRESHOLD_F:
            signals.append(RawSignal(
                source_name=self.name,
                source_type="weather",
                raw_data={
                    "event_type": "cold_front",
                    "temp_f": temp_f,
                    "feels_like_f": feels_like_f,
                    "location": location["name"],
                    "description": f"Cold front: {temp_f}°F (feels like {feels_like_f}°F)",
                    "business_impact": "Heating and winterization demand",
                    "niche_opportunities": ["HVAC", "plumbing", "landscaping"],
                },
                geography=location["name"],
            ))
        
        rain_1h = rain.get("1h", 0)
        if rain_1h >= self.HEAVY_RAIN_THRESHOLD_MM or "heavy rain" in description:
            signals.append(RawSignal(
                source_name=self.name,
                source_type="weather",
                raw_data={
                    "event_type": "heavy_rain",
                    "rain_mm": rain_1h,
                    "location": location["name"],
                    "description": f"Heavy rain in {location['name']}: {rain_1h}mm/hour",
                    "business_impact": "Roofing and water damage service demand",
                    "niche_opportunities": ["roofing", "water damage restoration", "plumbing"],
                },
                geography=location["name"],
            ))
        
        is_storm = any(kw in description or kw in weather_main for kw in ["storm", "thunder", "severe"])
        if is_storm:
            signals.append(RawSignal(
                source_name=self.name,
                source_type="weather",
                raw_data={
                    "event_type": "storm",
                    "weather_main": weather_main,
                    "description": f"Storm conditions in {location['name']}: {description}",
                    "location": location["name"],
                    "business_impact": "Storm damage and emergency services demand",
                    "niche_opportunities": ["roofing", "tree service", "restoration"],
                },
                geography=location["name"],
            ))
        
        return signals
    
    def _analyze_weather_alerts(self, data: Dict, location: Dict) -> List[RawSignal]:
        """Analyze weather alerts for hurricane/tropical storm signals."""
        signals = []
        
        alerts = data.get("alerts", [])
        for alert in alerts:
            event = alert.get("event", "").lower()
            description = alert.get("description", "")
            
            is_tropical = any(kw in event for kw in ["hurricane", "tropical", "storm warning"])
            
            if is_tropical:
                signals.append(RawSignal(
                    source_name=self.name,
                    source_type="weather",
                    raw_data={
                        "event_type": "hurricane_alert",
                        "alert_event": alert.get("event"),
                        "description": description[:500],
                        "sender": alert.get("sender_name"),
                        "location": location["name"],
                        "start": alert.get("start"),
                        "end": alert.get("end"),
                        "business_impact": "Hurricane preparation and post-storm services",
                        "niche_opportunities": ["roofing", "restoration", "generators", "tree service"],
                    },
                    geography=location["name"],
                ))
        
        return signals
    
    def parse(self, raw: RawSignal) -> ParsedSignal:
        """Parse weather signal into standardized format."""
        event_type = raw.raw_data.get("event_type", "weather")
        
        category_map = {
            "hurricane_alert": "HURRICANE_SEASON",
            "storm": "HURRICANE_SEASON",
            "extreme_heat": "OPPORTUNITY",
            "cold_front": "OPPORTUNITY",
            "heavy_rain": "HURRICANE_SEASON",
        }
        category = category_map.get(event_type, "OPPORTUNITY")
        
        niche_opportunities = raw.raw_data.get("niche_opportunities", [])
        niche_hint = niche_opportunities[0] if niche_opportunities else None
        
        context = raw.raw_data.get("description", "Weather event detected")
        business_impact = raw.raw_data.get("business_impact", "")
        if business_impact:
            context = f"{context} | Impact: {business_impact}"
        
        return ParsedSignal(
            source_type="weather",
            raw_payload=json.dumps(raw.raw_data),
            context_summary=context,
            geography=raw.geography,
            category_hint=category,
            niche_hint=niche_hint,
        )


class NewsSearchSignalSource(SignalSource):
    """
    Business news signal source for South Florida.
    
    Uses Google News RSS feeds (free, no API key required) to detect:
    - New business openings
    - Business expansions
    - Commercial developments
    - Industry news for target niches
    
    Falls back to RSS if NEWS_API_KEY is not set.
    """
    
    SEARCH_QUERIES = [
        "Miami new business opening",
        "Fort Lauderdale business expansion",
        "South Florida commercial development",
        "Miami HVAC company",
        "South Florida roofing contractor",
        "Miami med spa opening",
        "Broward County new business",
    ]
    
    GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"
    
    @property
    def name(self) -> str:
        return "news_search"
    
    @property
    def source_type(self) -> str:
        return "news"
    
    @property
    def enabled(self) -> bool:
        return SIGNAL_MODE in ("SANDBOX", "PRODUCTION")
    
    @property
    def cooldown_seconds(self) -> int:
        return 7200
    
    @property
    def max_items_per_run(self) -> int:
        return 25
    
    def fetch(self) -> List[RawSignal]:
        """Fetch news from Google News RSS feeds."""
        signals = []
        
        for query in self.SEARCH_QUERIES:
            try:
                articles = self._fetch_google_news_rss(query)
                for article in articles[:5]:
                    signals.append(RawSignal(
                        source_name=self.name,
                        source_type="news",
                        raw_data={
                            "title": article.get("title", ""),
                            "link": article.get("link", ""),
                            "published": article.get("published", ""),
                            "source": article.get("source", ""),
                            "query": query,
                        },
                        geography=self._extract_geography(article.get("title", "") + " " + query),
                    ))
                
                time.sleep(0.5)
                
            except Exception as e:
                print(f"[SIGNALNET][NEWS] Error fetching news for '{query}': {e}")
                continue
        
        print(f"[SIGNALNET][NEWS] Fetched {len(signals)} news signals from {len(self.SEARCH_QUERIES)} queries")
        return signals
    
    def _fetch_google_news_rss(self, query: str) -> List[Dict]:
        """Fetch news articles from Google News RSS feed."""
        import urllib.parse
        import xml.etree.ElementTree as ET
        
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"{self.GOOGLE_NEWS_RSS_BASE}?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; HossAgent/1.0; +https://hossagent.com)"
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            
            articles = []
            for item in root.findall(".//item"):
                title = item.find("title")
                link = item.find("link")
                pub_date = item.find("pubDate")
                source = item.find("source")
                
                articles.append({
                    "title": title.text if title is not None else "",
                    "link": link.text if link is not None else "",
                    "published": pub_date.text if pub_date is not None else "",
                    "source": source.text if source is not None else "",
                })
            
            return articles
            
        except Exception as e:
            print(f"[SIGNALNET][NEWS] RSS parse error: {e}")
            return []
    
    def _extract_geography(self, text: str) -> Optional[str]:
        """Extract geography from text."""
        text_lower = text.lower()
        
        if "miami" in text_lower or "dade" in text_lower:
            return "Miami"
        elif "fort lauderdale" in text_lower or "broward" in text_lower:
            return "Fort Lauderdale"
        elif "palm beach" in text_lower:
            return "Palm Beach"
        elif "south florida" in text_lower:
            return "South Florida"
        else:
            return "South Florida"
    
    def _infer_category(self, title: str, query: str) -> str:
        """Infer signal category from news content."""
        text_lower = (title + " " + query).lower()
        
        if any(kw in text_lower for kw in ["opening", "new business", "launches", "expands"]):
            return "GROWTH_SIGNAL"
        elif any(kw in text_lower for kw in ["competitor", "rivalry", "market share"]):
            return "COMPETITOR_SHIFT"
        elif any(kw in text_lower for kw in ["development", "construction", "project"]):
            return "GROWTH_SIGNAL"
        else:
            return "OPPORTUNITY"
    
    def _infer_niche(self, title: str, query: str) -> Optional[str]:
        """Infer niche from news content."""
        text_lower = (title + " " + query).lower()
        
        niche_keywords = {
            "hvac": ["hvac", "air conditioning", "heating", "cooling"],
            "roofing": ["roof", "roofing", "roofer"],
            "med spa": ["med spa", "medspa", "medical spa", "aesthetics", "botox"],
            "plumbing": ["plumb", "plumber", "plumbing"],
            "landscaping": ["landscape", "landscaping", "lawn"],
            "restaurant": ["restaurant", "dining", "food service"],
            "legal": ["attorney", "lawyer", "law firm", "legal"],
        }
        
        for niche, keywords in niche_keywords.items():
            if any(kw in text_lower for kw in keywords):
                return niche
        
        return None
    
    def parse(self, raw: RawSignal) -> ParsedSignal:
        """Parse news signal into standardized format."""
        title = raw.raw_data.get("title", "News article")
        query = raw.raw_data.get("query", "")
        source = raw.raw_data.get("source", "Unknown")
        
        category = self._infer_category(title, query)
        niche = self._infer_niche(title, query)
        
        context = f"News: {title}"
        if source:
            context = f"{context} (via {source})"
        
        return ParsedSignal(
            source_type="news",
            raw_payload=json.dumps(raw.raw_data),
            context_summary=context[:500],
            geography=raw.geography,
            category_hint=category,
            niche_hint=niche,
        )


class RedditSignalSource(SignalSource):
    """
    Reddit signal source for South Florida local business discussions.
    
    Uses Reddit's public JSON API (no authentication required) to monitor:
    - r/Miami, r/FortLauderdale, r/southflorida subreddits
    - Service recommendation requests
    - Business-related discussions
    - "Looking for" and "need help with" posts
    
    No API key required - uses public .json endpoints.
    """
    
    SUBREDDITS = ["Miami", "FortLauderdale", "southflorida"]
    
    SERVICE_KEYWORDS = [
        "recommend", "recommendation", "looking for",
        "need help", "anyone know", "best", "who do you use",
        "contractor", "plumber", "hvac", "ac", "air conditioning",
        "roofer", "roofing", "lawyer", "attorney", "doctor",
        "mechanic", "electrician", "handyman", "moving company",
    ]
    
    REDDIT_BASE_URL = "https://www.reddit.com"
    
    @property
    def name(self) -> str:
        return "reddit_local"
    
    @property
    def source_type(self) -> str:
        return "social"
    
    @property
    def enabled(self) -> bool:
        return SIGNAL_MODE in ("SANDBOX", "PRODUCTION")
    
    @property
    def cooldown_seconds(self) -> int:
        return 3600
    
    @property
    def max_items_per_run(self) -> int:
        return 30
    
    def fetch(self) -> List[RawSignal]:
        """Fetch relevant posts from South Florida subreddits."""
        signals = []
        
        for subreddit in self.SUBREDDITS:
            try:
                posts = self._fetch_subreddit_posts(subreddit)
                for post in posts:
                    if self._is_relevant_post(post):
                        geography = self._subreddit_to_geography(subreddit)
                        signals.append(RawSignal(
                            source_name=self.name,
                            source_type="social",
                            raw_data={
                                "title": post.get("title", ""),
                                "selftext": post.get("selftext", "")[:500],
                                "subreddit": subreddit,
                                "author": post.get("author", ""),
                                "score": post.get("score", 0),
                                "num_comments": post.get("num_comments", 0),
                                "permalink": post.get("permalink", ""),
                                "created_utc": post.get("created_utc", 0),
                                "url": f"{self.REDDIT_BASE_URL}{post.get('permalink', '')}",
                            },
                            geography=geography,
                        ))
                
                time.sleep(2)
                
            except Exception as e:
                print(f"[SIGNALNET][REDDIT] Error fetching r/{subreddit}: {e}")
                continue
        
        print(f"[SIGNALNET][REDDIT] Fetched {len(signals)} relevant posts from {len(self.SUBREDDITS)} subreddits")
        return signals
    
    def _fetch_subreddit_posts(self, subreddit: str, limit: int = 50) -> List[Dict]:
        """Fetch recent posts from a subreddit using public JSON API."""
        try:
            url = f"{self.REDDIT_BASE_URL}/r/{subreddit}/new.json"
            params = {"limit": limit}
            headers = {
                "User-Agent": "HossAgent/1.0 (Business Signal Detection; +https://hossagent.com)"
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            posts = []
            
            for child in data.get("data", {}).get("children", []):
                posts.append(child.get("data", {}))
            
            return posts
            
        except requests.RequestException as e:
            print(f"[SIGNALNET][REDDIT] API error for r/{subreddit}: {e}")
            return []
    
    def _is_relevant_post(self, post: Dict) -> bool:
        """Check if a post is relevant for business signals."""
        title = post.get("title", "").lower()
        selftext = post.get("selftext", "").lower()
        content = title + " " + selftext
        
        return any(keyword in content for keyword in self.SERVICE_KEYWORDS)
    
    def _subreddit_to_geography(self, subreddit: str) -> str:
        """Map subreddit to geography."""
        mapping = {
            "Miami": "Miami",
            "FortLauderdale": "Fort Lauderdale",
            "southflorida": "South Florida",
        }
        return mapping.get(subreddit, "South Florida")
    
    def _extract_niche(self, title: str, selftext: str) -> Optional[str]:
        """Extract potential business niche from post content."""
        content = (title + " " + selftext).lower()
        
        niche_patterns = {
            "HVAC": ["hvac", "ac ", "a/c", "air conditioning", "heating", "cooling"],
            "Roofing": ["roof", "roofing", "roofer", "shingles"],
            "Plumbing": ["plumber", "plumbing", "pipe", "drain", "water heater"],
            "Electrical": ["electrician", "electrical", "wiring"],
            "Legal": ["lawyer", "attorney", "legal"],
            "Medical": ["doctor", "clinic", "medical", "dentist"],
            "Automotive": ["mechanic", "auto", "car repair"],
            "Home Services": ["handyman", "contractor", "renovation", "remodel"],
            "Moving": ["moving company", "movers", "relocation"],
        }
        
        for niche, keywords in niche_patterns.items():
            if any(kw in content for kw in keywords):
                return niche
        
        return None
    
    def _infer_category(self, post: Dict) -> str:
        """Infer signal category from post content."""
        title = post.get("title", "").lower()
        selftext = post.get("selftext", "").lower()
        content = title + " " + selftext
        
        if any(kw in content for kw in ["recommend", "looking for", "anyone know", "who do you use"]):
            return "OPPORTUNITY"
        elif any(kw in content for kw in ["need help", "urgent", "emergency"]):
            return "OPPORTUNITY"
        elif any(kw in content for kw in ["new business", "opening", "just opened"]):
            return "GROWTH_SIGNAL"
        else:
            return "OPPORTUNITY"
    
    def parse(self, raw: RawSignal) -> ParsedSignal:
        """Parse Reddit post into standardized format."""
        title = raw.raw_data.get("title", "Reddit post")
        selftext = raw.raw_data.get("selftext", "")
        subreddit = raw.raw_data.get("subreddit", "")
        score = raw.raw_data.get("score", 0)
        num_comments = raw.raw_data.get("num_comments", 0)
        
        category = self._infer_category(raw.raw_data)
        niche = self._extract_niche(title, selftext)
        
        context = f"Reddit r/{subreddit}: {title}"
        engagement = f"(Score: {score}, Comments: {num_comments})"
        context = f"{context} {engagement}"
        
        return ParsedSignal(
            source_type="social",
            raw_payload=json.dumps(raw.raw_data),
            context_summary=context[:500],
            geography=raw.geography,
            category_hint=category,
            niche_hint=niche,
        )


register_source_class(WeatherSignalSource)
register_source_class(NewsSearchSignalSource)
register_source_class(RedditSignalSource)
register_source_class(SyntheticSignalSource)

print("[SIGNALNET][STARTUP] Real signal sources registered: weather_openweather, news_search, reddit_local")
