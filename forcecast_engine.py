"""
ForceCast Mapping Engine for HossAgent - EPIC 5

Maps MacroEvents (big-company moves from SEC filings, earnings calls, etc.)
to SMB target profiles that represent downstream opportunities.

Example Flow:
1. MacroEvent: "McDonald's plans 120 new Florida locations over 3 years"
2. ForceCast identifies affected SMB segments:
   - HVAC contractors (new builds need AC)
   - Electrical contractors (commercial wiring)
   - Plumbing contractors
   - Construction suppliers
   - Staffing agencies
   - Commercial cleaning services
3. Generates LeadEvents for each SMB segment in affected geographies

This creates a strategic intelligence layer that connects big-company
moves to small business opportunities.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from models import (
    MacroEvent,
    LeadEvent,
    Signal,
    Company,
    MACRO_FORCE_TYPE_EXPANSION,
    MACRO_FORCE_TYPE_CONTRACTION,
    MACRO_FORCE_TYPE_RESTRUCTURING,
    MACRO_FORCE_TYPE_MERGER,
    MACRO_FORCE_TYPE_BANKRUPTCY,
    MACRO_FORCE_TYPE_SUPPLY_CHAIN,
    MACRO_FORCE_TYPE_REGULATORY,
    ENRICHMENT_STATUS_UNENRICHED,
)


FORCE_TYPE_SMB_MAPPINGS = {
    MACRO_FORCE_TYPE_EXPANSION: {
        "primary_segments": [
            "hvac_contractor",
            "electrical_contractor",
            "plumbing_contractor",
            "general_contractor",
            "commercial_construction",
            "staffing_agency",
            "commercial_cleaning",
            "security_services",
            "landscaping",
            "signage_company",
        ],
        "secondary_segments": [
            "commercial_real_estate",
            "equipment_supplier",
            "office_furniture",
            "it_services",
            "catering_services",
            "uniform_supplier",
        ],
        "urgency_multiplier": 1.2,
        "opportunity_type": "growth_opportunity",
    },
    MACRO_FORCE_TYPE_CONTRACTION: {
        "primary_segments": [
            "liquidation_services",
            "commercial_real_estate",
            "business_broker",
            "equipment_resale",
            "staffing_agency",
            "outplacement_services",
        ],
        "secondary_segments": [
            "moving_services",
            "storage_facilities",
            "legal_services",
        ],
        "urgency_multiplier": 1.5,
        "opportunity_type": "market_shift",
    },
    MACRO_FORCE_TYPE_MERGER: {
        "primary_segments": [
            "it_integration",
            "hr_consulting",
            "legal_services",
            "branding_agency",
            "commercial_construction",
            "office_furniture",
        ],
        "secondary_segments": [
            "staffing_agency",
            "training_services",
            "security_services",
        ],
        "urgency_multiplier": 1.3,
        "opportunity_type": "competitor_intel",
    },
    MACRO_FORCE_TYPE_BANKRUPTCY: {
        "primary_segments": [
            "liquidation_services",
            "business_broker",
            "legal_services",
            "commercial_real_estate",
            "equipment_resale",
        ],
        "secondary_segments": [
            "staffing_agency",
            "moving_services",
            "storage_facilities",
        ],
        "urgency_multiplier": 1.8,
        "opportunity_type": "market_shift",
    },
    MACRO_FORCE_TYPE_SUPPLY_CHAIN: {
        "primary_segments": [
            "logistics_provider",
            "warehouse_services",
            "freight_broker",
            "equipment_supplier",
            "manufacturing_services",
        ],
        "secondary_segments": [
            "it_services",
            "consulting_services",
            "distribution_services",
        ],
        "urgency_multiplier": 1.4,
        "opportunity_type": "growth_opportunity",
    },
    MACRO_FORCE_TYPE_RESTRUCTURING: {
        "primary_segments": [
            "consulting_services",
            "hr_consulting",
            "it_services",
            "legal_services",
            "staffing_agency",
        ],
        "secondary_segments": [
            "training_services",
            "commercial_construction",
            "office_furniture",
        ],
        "urgency_multiplier": 1.1,
        "opportunity_type": "market_shift",
    },
    MACRO_FORCE_TYPE_REGULATORY: {
        "primary_segments": [
            "compliance_consulting",
            "legal_services",
            "it_services",
            "training_services",
            "environmental_services",
        ],
        "secondary_segments": [
            "insurance_broker",
            "hr_consulting",
            "safety_consulting",
        ],
        "urgency_multiplier": 1.0,
        "opportunity_type": "market_shift",
    },
}

SEGMENT_TO_NICHE = {
    "hvac_contractor": "HVAC",
    "electrical_contractor": "Electrical",
    "plumbing_contractor": "Plumbing",
    "general_contractor": "Construction",
    "commercial_construction": "Construction",
    "staffing_agency": "Staffing",
    "commercial_cleaning": "Commercial Cleaning",
    "security_services": "Security",
    "landscaping": "Landscaping",
    "signage_company": "Signage",
    "commercial_real_estate": "Commercial Real Estate",
    "equipment_supplier": "Equipment Supply",
    "office_furniture": "Office Furniture",
    "it_services": "IT Services",
    "catering_services": "Catering",
    "uniform_supplier": "Uniform Supply",
    "liquidation_services": "Liquidation",
    "business_broker": "Business Brokerage",
    "equipment_resale": "Equipment Resale",
    "outplacement_services": "HR Services",
    "moving_services": "Moving Services",
    "storage_facilities": "Storage",
    "legal_services": "Legal",
    "it_integration": "IT Integration",
    "hr_consulting": "HR Consulting",
    "branding_agency": "Marketing Agency",
    "training_services": "Training",
    "logistics_provider": "Logistics",
    "warehouse_services": "Warehousing",
    "freight_broker": "Freight Brokerage",
    "manufacturing_services": "Manufacturing",
    "consulting_services": "Consulting",
    "distribution_services": "Distribution",
    "compliance_consulting": "Compliance Consulting",
    "environmental_services": "Environmental Services",
    "insurance_broker": "Insurance",
    "safety_consulting": "Safety Consulting",
}

SOUTH_FLORIDA_GEOS = [
    "Miami",
    "Miami-Dade",
    "Fort Lauderdale",
    "Broward",
    "West Palm Beach",
    "Palm Beach",
    "Boca Raton",
    "Hollywood",
    "Hialeah",
    "Coral Gables",
    "Doral",
    "South Florida",
    "Florida",
]


@dataclass
class SMBTarget:
    """A potential SMB target derived from a MacroEvent."""
    segment: str
    niche: str
    geography: str
    urgency_score: int
    opportunity_type: str
    macro_event_id: int
    macro_headline: str
    company_name: str
    time_horizon: Optional[str] = None
    recommended_action: Optional[str] = None
    is_primary: bool = True


@dataclass
class ForceCastResult:
    """Result of ForceCast mapping for a single MacroEvent."""
    macro_event_id: int
    force_type: str
    targets_generated: int
    primary_targets: List[SMBTarget] = field(default_factory=list)
    secondary_targets: List[SMBTarget] = field(default_factory=list)
    geographies_covered: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "macro_event_id": self.macro_event_id,
            "force_type": self.force_type,
            "targets_generated": self.targets_generated,
            "primary_count": len(self.primary_targets),
            "secondary_count": len(self.secondary_targets),
            "geographies": self.geographies_covered,
        }


def _parse_geographies(geo_json: Optional[str]) -> List[str]:
    """Parse geographies JSON field from MacroEvent."""
    if not geo_json:
        return ["South Florida"]
    
    try:
        geos = json.loads(geo_json)
        if isinstance(geos, list):
            return geos
        return ["South Florida"]
    except json.JSONDecodeError:
        return ["South Florida"]


def _filter_south_florida_geos(geos: List[str]) -> List[str]:
    """Filter geographies to South Florida regions only."""
    result = []
    for geo in geos:
        geo_lower = geo.lower()
        for sfla_geo in SOUTH_FLORIDA_GEOS:
            if sfla_geo.lower() in geo_lower or geo_lower in sfla_geo.lower():
                result.append(geo)
                break
    
    return result if result else ["South Florida"]


def _calculate_urgency_score(
    base_score: int,
    multiplier: float,
    is_primary: bool,
    time_horizon: Optional[str]
) -> int:
    """Calculate urgency score for an SMB target."""
    score = base_score * multiplier
    
    if not is_primary:
        score *= 0.8
    
    if time_horizon:
        horizon_lower = time_horizon.lower()
        if "0-12" in horizon_lower or "immediate" in horizon_lower:
            score *= 1.2
        elif "1-3" in horizon_lower:
            score *= 1.0
        elif "3-5" in horizon_lower:
            score *= 0.8
    
    return min(100, max(10, int(score)))


def _generate_recommended_action(
    segment: str,
    force_type: str,
    company_name: str,
    geography: str
) -> str:
    """Generate a recommended action string for the SMB target."""
    niche = SEGMENT_TO_NICHE.get(segment, segment.replace("_", " ").title())
    
    actions = {
        MACRO_FORCE_TYPE_EXPANSION: f"Reach out to {niche} businesses in {geography}. {company_name}'s expansion creates new commercial opportunities.",
        MACRO_FORCE_TYPE_CONTRACTION: f"Alert {niche} providers in {geography}. {company_name}'s contraction may release business to local competitors.",
        MACRO_FORCE_TYPE_MERGER: f"Target {niche} companies in {geography}. {company_name}'s merger creates integration and transition opportunities.",
        MACRO_FORCE_TYPE_BANKRUPTCY: f"Urgent: {niche} providers in {geography} may pick up business from {company_name}'s bankruptcy.",
        MACRO_FORCE_TYPE_SUPPLY_CHAIN: f"Contact {niche} businesses in {geography}. {company_name}'s supply chain changes may require new local partners.",
        MACRO_FORCE_TYPE_RESTRUCTURING: f"Reach {niche} providers in {geography}. {company_name}'s restructuring may need outside expertise.",
        MACRO_FORCE_TYPE_REGULATORY: f"Inform {niche} businesses in {geography}. Regulatory changes at {company_name} may affect the local market.",
    }
    
    return actions.get(force_type, f"Research {niche} opportunities in {geography} related to {company_name}.")


def map_macro_event_to_smb_targets(
    macro_event: MacroEvent,
    include_secondary: bool = True,
    limit_per_segment: int = 3
) -> ForceCastResult:
    """
    Map a single MacroEvent to potential SMB targets.
    
    Args:
        macro_event: The MacroEvent to map
        include_secondary: Whether to include secondary (lower priority) segments
        limit_per_segment: Max targets per segment per geography
    
    Returns:
        ForceCastResult with all generated SMB targets
    """
    force_type = macro_event.force_type
    mapping = FORCE_TYPE_SMB_MAPPINGS.get(force_type, FORCE_TYPE_SMB_MAPPINGS[MACRO_FORCE_TYPE_EXPANSION])
    
    raw_geos = _parse_geographies(macro_event.geographies)
    geographies = _filter_south_florida_geos(raw_geos)
    
    primary_targets = []
    secondary_targets = []
    
    base_score = 70
    multiplier = mapping.get("urgency_multiplier", 1.0)
    opportunity_type = mapping.get("opportunity_type", "growth_opportunity")
    
    for segment in mapping.get("primary_segments", []):
        niche = SEGMENT_TO_NICHE.get(segment, segment.replace("_", " ").title())
        
        for geo in geographies[:limit_per_segment]:
            urgency = _calculate_urgency_score(base_score, multiplier, True, macro_event.time_horizon)
            action = _generate_recommended_action(segment, force_type, macro_event.company_name, geo)
            
            target = SMBTarget(
                segment=segment,
                niche=niche,
                geography=geo,
                urgency_score=urgency,
                opportunity_type=opportunity_type,
                macro_event_id=macro_event.id,
                macro_headline=macro_event.headline,
                company_name=macro_event.company_name,
                time_horizon=macro_event.time_horizon,
                recommended_action=action,
                is_primary=True,
            )
            primary_targets.append(target)
    
    if include_secondary:
        for segment in mapping.get("secondary_segments", []):
            niche = SEGMENT_TO_NICHE.get(segment, segment.replace("_", " ").title())
            
            for geo in geographies[:limit_per_segment]:
                urgency = _calculate_urgency_score(base_score, multiplier, False, macro_event.time_horizon)
                action = _generate_recommended_action(segment, force_type, macro_event.company_name, geo)
                
                target = SMBTarget(
                    segment=segment,
                    niche=niche,
                    geography=geo,
                    urgency_score=urgency,
                    opportunity_type=opportunity_type,
                    macro_event_id=macro_event.id,
                    macro_headline=macro_event.headline,
                    company_name=macro_event.company_name,
                    time_horizon=macro_event.time_horizon,
                    recommended_action=action,
                    is_primary=False,
                )
                secondary_targets.append(target)
    
    return ForceCastResult(
        macro_event_id=macro_event.id,
        force_type=force_type,
        targets_generated=len(primary_targets) + len(secondary_targets),
        primary_targets=primary_targets,
        secondary_targets=secondary_targets,
        geographies_covered=geographies,
    )


def create_lead_events_from_targets(
    session: Session,
    targets: List[SMBTarget],
    dry_run: bool = False
) -> List[LeadEvent]:
    """
    Create LeadEvent records from SMB targets.
    
    Args:
        session: Database session
        targets: List of SMBTarget objects
        dry_run: If True, don't persist to database
    
    Returns:
        List of created LeadEvent objects
    """
    lead_events = []
    
    for target in targets:
        summary = f"MacroStorm Alert: {target.macro_headline}"
        if target.geography and target.geography != "South Florida":
            summary += f" ({target.geography})"
        
        lead_event = LeadEvent(
            macro_event_id=target.macro_event_id,
            lead_company=f"{target.niche} businesses in {target.geography}",
            summary=summary,
            category=target.opportunity_type.upper(),
            urgency_score=target.urgency_score,
            status="NEW",
            enrichment_status=ENRICHMENT_STATUS_UNENRICHED,
            recommended_action=target.recommended_action,
        )
        
        if not dry_run:
            session.add(lead_event)
            lead_events.append(lead_event)
            print(f"[FORCECAST][LEAD_EVENT] Created: {target.niche} in {target.geography}")
        else:
            print(f"[FORCECAST][DRY_RUN] Would create: {target.niche} in {target.geography}")
    
    if not dry_run and lead_events:
        session.commit()
    
    return lead_events


def process_unprocessed_macro_events(
    session: Session,
    limit: int = 10,
    include_secondary: bool = True,
    dry_run: bool = False
) -> Dict:
    """
    Process unprocessed MacroEvents and generate LeadEvents.
    
    Args:
        session: Database session
        limit: Max MacroEvents to process
        include_secondary: Include secondary SMB segments
        dry_run: Skip database writes
    
    Returns:
        Summary dict with counts
    """
    print("[FORCECAST][START] Processing unprocessed MacroEvents")
    
    macro_events = session.exec(
        select(MacroEvent)
        .where(MacroEvent.processed == False)
        .order_by(MacroEvent.created_at.desc())
        .limit(limit)
    ).all()
    
    if not macro_events:
        print("[FORCECAST][SKIP] No unprocessed MacroEvents found")
        return {
            "macro_events_processed": 0,
            "targets_generated": 0,
            "lead_events_created": 0,
        }
    
    total_targets = 0
    total_lead_events = 0
    
    for macro_event in macro_events:
        print(f"[FORCECAST][PROCESS] MacroEvent {macro_event.id}: {macro_event.headline[:50]}...")
        
        result = map_macro_event_to_smb_targets(
            macro_event,
            include_secondary=include_secondary,
        )
        
        all_targets = result.primary_targets + result.secondary_targets
        total_targets += len(all_targets)
        
        lead_events = create_lead_events_from_targets(session, all_targets, dry_run)
        total_lead_events += len(lead_events)
        
        if not dry_run:
            macro_event.processed = True
            macro_event.processed_at = datetime.utcnow()
            macro_event.leads_generated = len(lead_events)
            session.add(macro_event)
    
    if not dry_run:
        session.commit()
    
    result = {
        "macro_events_processed": len(macro_events),
        "targets_generated": total_targets,
        "lead_events_created": total_lead_events,
        "dry_run": dry_run,
    }
    
    print(f"[FORCECAST][COMPLETE] {result}")
    return result


def get_forcecast_analytics(session: Session) -> Dict:
    """
    Get analytics on ForceCast performance.
    
    Returns:
        Dict with analytics data
    """
    total_macro = session.exec(select(MacroEvent)).all()
    processed_macro = [m for m in total_macro if m.processed]
    
    total_leads_generated = sum(m.leads_generated for m in processed_macro)
    total_leads_enriched = sum(m.leads_enriched for m in processed_macro)
    total_leads_contacted = sum(m.leads_contacted for m in processed_macro)
    total_leads_replied = sum(m.leads_replied for m in processed_macro)
    
    force_type_breakdown = {}
    for m in total_macro:
        ft = m.force_type
        if ft not in force_type_breakdown:
            force_type_breakdown[ft] = {"count": 0, "leads_generated": 0}
        force_type_breakdown[ft]["count"] += 1
        force_type_breakdown[ft]["leads_generated"] += m.leads_generated
    
    return {
        "total_macro_events": len(total_macro),
        "processed_macro_events": len(processed_macro),
        "unprocessed_macro_events": len(total_macro) - len(processed_macro),
        "total_leads_generated": total_leads_generated,
        "total_leads_enriched": total_leads_enriched,
        "total_leads_contacted": total_leads_contacted,
        "total_leads_replied": total_leads_replied,
        "conversion_rate": round(total_leads_enriched / total_leads_generated * 100, 2) if total_leads_generated > 0 else 0,
        "force_type_breakdown": force_type_breakdown,
    }


run_forcecast_for_new_macro_events = process_unprocessed_macro_events

print("[FORCECAST][STARTUP] ForceCast Mapping Engine loaded - EPIC 5")
