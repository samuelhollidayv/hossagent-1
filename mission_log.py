"""
ARCHANGEL v2: Mission Log System

Per-lead enrichment history tracking - the "black box recorder" for enrichment attempts.
Prevents re-doing the same queries and provides ML-ready structured logs.

Each enrichment subroutine (NameStorm/DomainStorm/PhoneStorm/EmailStorm) writes structured entries.
Before doing something "expensive" (search, scrape), check log to avoid exact repeats.
"""

import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict


@dataclass
class MissionLogEntry:
    """Single enrichment attempt record."""
    timestamp: str
    pass_number: int
    phase: str  # NAMESTORM, DOMAINSTORM, PHONESTORM, EMAILSTORM
    action: str  # duckduckgo_search, page_scrape, email_validation, etc.
    query: Optional[str] = None  # Search query or URL attempted
    result: str = "pending"  # success, no_result, error, skipped, cached
    notes: Optional[str] = None  # Additional context
    duration_ms: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class MissionLog:
    """
    Mission log manager for a single lead's enrichment history.
    
    Usage:
        log = MissionLog.from_json(lead_event.enrichment_mission_log)
        
        if not log.has_attempted("DOMAINSTORM", "duckduckgo_search", "cool running air miami"):
            # Do the search
            result = search_duckduckgo("cool running air miami")
            log.add_entry(
                phase="DOMAINSTORM",
                action="duckduckgo_search",
                query="cool running air miami",
                result="success" if result else "no_result"
            )
        
        lead_event.enrichment_mission_log = log.to_json()
    """
    
    def __init__(self, entries: Optional[List[MissionLogEntry]] = None):
        self.entries: List[MissionLogEntry] = entries or []
        self._current_pass: int = 1
    
    @classmethod
    def from_json(cls, json_str: Optional[str]) -> "MissionLog":
        """Parse mission log from JSON string."""
        if not json_str:
            return cls()
        
        try:
            data = json.loads(json_str)
            entries = []
            max_pass = 0
            
            for entry_dict in data:
                entry = MissionLogEntry(
                    timestamp=entry_dict.get("timestamp", ""),
                    pass_number=entry_dict.get("pass_number", entry_dict.get("pass", 1)),
                    phase=entry_dict.get("phase", "UNKNOWN"),
                    action=entry_dict.get("action", "unknown"),
                    query=entry_dict.get("query"),
                    result=entry_dict.get("result", "pending"),
                    notes=entry_dict.get("notes"),
                    duration_ms=entry_dict.get("duration_ms", 0)
                )
                entries.append(entry)
                max_pass = max(max_pass, entry.pass_number)
            
            log = cls(entries)
            log._current_pass = max_pass if max_pass > 0 else 1
            return log
            
        except (json.JSONDecodeError, TypeError, KeyError):
            return cls()
    
    def to_json(self) -> str:
        """Serialize mission log to JSON string."""
        return json.dumps([e.to_dict() for e in self.entries], default=str)
    
    def add_entry(
        self,
        phase: str,
        action: str,
        query: Optional[str] = None,
        result: str = "pending",
        notes: Optional[str] = None,
        duration_ms: int = 0
    ) -> MissionLogEntry:
        """Add a new entry to the mission log."""
        entry = MissionLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            pass_number=self._current_pass,
            phase=phase,
            action=action,
            query=query,
            result=result,
            notes=notes,
            duration_ms=duration_ms
        )
        self.entries.append(entry)
        return entry
    
    def start_new_pass(self) -> int:
        """Increment pass number for a new enrichment cycle."""
        self._current_pass += 1
        return self._current_pass
    
    @property
    def current_pass(self) -> int:
        return self._current_pass
    
    def has_attempted(self, phase: str, action: str, query: Optional[str] = None) -> bool:
        """Check if this exact phase/action/query combination was already tried."""
        for entry in self.entries:
            if entry.phase == phase and entry.action == action:
                if query is None or entry.query == query:
                    return True
        return False
    
    def has_succeeded(self, phase: str, action: Optional[str] = None) -> bool:
        """Check if any action in this phase succeeded."""
        for entry in self.entries:
            if entry.phase == phase and entry.result == "success":
                if action is None or entry.action == action:
                    return True
        return False
    
    def get_attempts_for_phase(self, phase: str) -> List[MissionLogEntry]:
        """Get all attempts for a specific phase."""
        return [e for e in self.entries if e.phase == phase]
    
    def get_failed_queries(self, phase: str, action: str) -> List[str]:
        """Get list of queries that failed for this phase/action."""
        return [
            e.query for e in self.entries
            if e.phase == phase and e.action == action 
            and e.result in ("no_result", "error") and e.query
        ]
    
    def count_attempts(self, phase: Optional[str] = None) -> int:
        """Count total attempts, optionally filtered by phase."""
        if phase:
            return len([e for e in self.entries if e.phase == phase])
        return len(self.entries)
    
    def get_last_entry(self, phase: Optional[str] = None) -> Optional[MissionLogEntry]:
        """Get the most recent entry, optionally filtered by phase."""
        filtered = self.entries if phase is None else [e for e in self.entries if e.phase == phase]
        return filtered[-1] if filtered else None
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics of the mission log."""
        phases = {}
        for entry in self.entries:
            if entry.phase not in phases:
                phases[entry.phase] = {"attempts": 0, "successes": 0, "failures": 0}
            phases[entry.phase]["attempts"] += 1
            if entry.result == "success":
                phases[entry.phase]["successes"] += 1
            elif entry.result in ("no_result", "error"):
                phases[entry.phase]["failures"] += 1
        
        return {
            "total_entries": len(self.entries),
            "current_pass": self._current_pass,
            "phases": phases
        }
    
    def get_condensed_view(self, limit: int = 5) -> List[Dict]:
        """Get condensed view of last N entries for UI display."""
        recent = self.entries[-limit:] if len(self.entries) > limit else self.entries
        return [
            {
                "pass": e.pass_number,
                "phase": e.phase,
                "action": e.action,
                "result": e.result,
                "notes": e.notes[:50] if e.notes else None
            }
            for e in recent
        ]


def log_enrichment_attempt(
    mission_log: MissionLog,
    phase: str,
    action: str,
    query: Optional[str] = None,
    result: str = "pending",
    notes: Optional[str] = None,
    duration_ms: int = 0
) -> MissionLog:
    """
    Convenience function to log an enrichment attempt.
    
    Returns the updated mission log for chaining.
    """
    mission_log.add_entry(
        phase=phase,
        action=action,
        query=query,
        result=result,
        notes=notes,
        duration_ms=duration_ms
    )
    return mission_log


def should_attempt_action(
    mission_log: MissionLog,
    phase: str,
    action: str,
    query: Optional[str] = None,
    max_retries: int = 1
) -> bool:
    """
    Check if we should attempt an action based on mission log history.
    
    Returns False if:
    - This exact query was already tried and succeeded
    - This exact query was tried max_retries times
    """
    attempts = [
        e for e in mission_log.entries
        if e.phase == phase and e.action == action
        and (query is None or e.query == query)
    ]
    
    for attempt in attempts:
        if attempt.result == "success":
            return False
    
    if len(attempts) >= max_retries:
        return False
    
    return True
