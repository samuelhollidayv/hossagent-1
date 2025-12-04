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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Type
from sqlmodel import Session, select

from models import Signal, LeadEvent


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
