"""
Outbound utilities for HossAgent.
Handles business profile lookups, do-not-contact checks, and pending outbound creation.
"""
import json
from typing import Optional, List
from datetime import datetime
from sqlmodel import Session, select
from models import BusinessProfile, PendingOutbound, Customer, Signal


def _get_source_url_for_lead_event(session: Session, lead_event) -> Optional[str]:
    """
    Get source URL from the Signal associated with a LeadEvent.
    
    Extracts URL from signal's raw_payload (stored as JSON).
    
    Args:
        session: Database session
        lead_event: LeadEvent object with signal_id
        
    Returns:
        Source URL string if found, None otherwise
    """
    if not lead_event.signal_id:
        return None
    
    signal = session.exec(
        select(Signal).where(Signal.id == lead_event.signal_id)
    ).first()
    
    if not signal or not signal.raw_payload:
        return None
    
    try:
        payload = json.loads(signal.raw_payload)
        return payload.get("url") or payload.get("source_url") or payload.get("link")
    except (json.JSONDecodeError, TypeError):
        return None


def get_business_profile(session: Session, customer_id: int) -> Optional[BusinessProfile]:
    """
    Fetch BusinessProfile for a customer.
    
    Args:
        session: Database session
        customer_id: The customer ID to fetch profile for
        
    Returns:
        BusinessProfile if found, None otherwise
    """
    return session.exec(
        select(BusinessProfile).where(BusinessProfile.customer_id == customer_id)
    ).first()


def parse_do_not_contact_list(do_not_contact_list: Optional[str]) -> List[str]:
    """
    Parse do_not_contact_list which can be JSON array or comma-separated string.
    
    Args:
        do_not_contact_list: Raw string from BusinessProfile
        
    Returns:
        List of emails/domains to block
    """
    if not do_not_contact_list:
        return []
    
    do_not_contact_list = do_not_contact_list.strip()
    if not do_not_contact_list:
        return []
    
    if do_not_contact_list.startswith('['):
        try:
            parsed = json.loads(do_not_contact_list)
            if isinstance(parsed, list):
                return [str(item).strip().lower() for item in parsed if item]
        except json.JSONDecodeError:
            pass
    
    return [item.strip().lower() for item in do_not_contact_list.split(',') if item.strip()]


def check_do_not_contact(email: str, do_not_contact_list: Optional[str]) -> bool:
    """
    Check if an email or its domain is in the do-not-contact list.
    
    Args:
        email: Email address to check
        do_not_contact_list: Raw string from BusinessProfile (JSON array or comma-separated)
        
    Returns:
        True if email/domain is blocked, False otherwise
    """
    if not email or not do_not_contact_list:
        return False
    
    email = email.strip().lower()
    blocked_entries = parse_do_not_contact_list(do_not_contact_list)
    
    if not blocked_entries:
        return False
    
    email_domain = email.split('@')[-1] if '@' in email else ''
    
    for entry in blocked_entries:
        if entry == email:
            return True
        if entry.startswith('@') and email.endswith(entry):
            return True
        if not entry.startswith('@') and entry == email_domain:
            return True
        if '@' not in entry and email_domain == entry:
            return True
    
    return False


def create_pending_outbound(
    session: Session,
    customer_id: Optional[int],
    lead_id: Optional[int],
    to_email: str,
    to_name: Optional[str],
    subject: str,
    body: str,
    context_summary: Optional[str] = None,
    lead_event_id: Optional[int] = None,
    status: str = "PENDING"
) -> PendingOutbound:
    """
    Create a PendingOutbound record.
    
    Used for both REVIEW mode (status=PENDING) and AUTO mode (status=SENT).
    This ensures portal can always find email content.
    
    Args:
        session: Database session
        customer_id: The customer ID this outbound belongs to (optional for AUTO)
        lead_id: Optional lead ID this outbound is for
        to_email: Recipient email address
        to_name: Recipient name
        subject: Email subject
        body: Email body
        context_summary: Why this email is being sent
        lead_event_id: Optional lead event ID this outbound is for
        status: Initial status - PENDING for review, SENT for already sent
        
    Returns:
        Created PendingOutbound record
    """
    pending = PendingOutbound(
        customer_id=customer_id,
        lead_id=lead_id,
        lead_event_id=lead_event_id,
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        body=body,
        context_summary=context_summary,
        status=status,
        created_at=datetime.utcnow()
    )
    session.add(pending)
    session.flush()
    
    status_str = "QUEUED" if status == "PENDING" else status
    print(f"[OUTBOUND] Created PendingOutbound {pending.id} ({status_str}) for customer {customer_id}: to={to_email} subject=\"{subject[:50]}...\"")
    
    return pending


def get_customer_by_id(session: Session, customer_id: int) -> Optional[Customer]:
    """
    Fetch a Customer by ID.
    
    Args:
        session: Database session
        customer_id: The customer ID to fetch
        
    Returns:
        Customer if found, None otherwise
    """
    return session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()


class ImmediateSendResult:
    """Result of an immediate send operation."""
    def __init__(
        self,
        success: bool,
        action: str,
        reason: str = "",
        email_sent: bool = False,
        queued_for_review: bool = False
    ):
        self.success = success
        self.action = action
        self.reason = reason
        self.email_sent = email_sent
        self.queued_for_review = queued_for_review
    
    def __repr__(self):
        return f"ImmediateSendResult(success={self.success}, action='{self.action}', reason='{self.reason}')"


def send_lead_event_immediate(session: Session, lead_event, commit: bool = True) -> ImmediateSendResult:
    """
    Immediately send outbound email for a LeadEvent that has been enriched with an email.
    
    This is the core function that eliminates the waiting state between enrichment and sending.
    Called directly from the enrichment pipeline when an email is discovered.
    
    Flow:
    - AUTO mode: Send email immediately, update status to OUTBOUND_SENT
    - REVIEW mode: Create PendingOutbound for customer approval
    
    Args:
        session: Database session
        lead_event: LeadEvent with lead_email set
        commit: Whether to commit the transaction (default True)
        
    Returns:
        ImmediateSendResult with success status and action taken
    """
    import hashlib
    from datetime import datetime
    from models import (
        OUTREACH_MODE_AUTO, OUTREACH_MODE_REVIEW,
        LEAD_STATUS_NEW, LEAD_STATUS_CONTACTED,
        NEXT_STEP_OWNER_AGENT, NEXT_STEP_OWNER_CUSTOMER,
        ENRICHMENT_STATUS_OUTBOUND_SENT,
    )
    from email_utils import send_email
    
    contact_email = lead_event.lead_email or lead_event.enriched_email
    if not contact_email:
        return ImmediateSendResult(
            success=False,
            action="skipped",
            reason="No email address available"
        )
    
    GENERIC_PREFIXES = ['info', 'contact', 'hello', 'support', 'admin', 'sales', 'enquiry', 
                        'enquiries', 'office', 'mail', 'help', 'general', 'team']
    email_local = contact_email.split('@')[0].lower() if '@' in contact_email else ''
    is_generic_inbox = any(email_local == prefix or email_local.startswith(f"{prefix}.") 
                           for prefix in GENERIC_PREFIXES)
    
    email_confidence = getattr(lead_event, 'email_confidence', 0.5)
    
    if is_generic_inbox:
        if email_confidence < 0.4:
            print(f"[IMMEDIATE-SEND] Skipping low-confidence generic inbox {contact_email} (confidence={email_confidence:.2f})")
            return ImmediateSendResult(
                success=False,
                action="skipped",
                reason=f"Generic inbox ({email_local}@) with low confidence - continuing enrichment"
            )
        else:
            print(f"[IMMEDIATE-SEND] Allowing generic inbox {contact_email} (confidence={email_confidence:.2f}) - no person-like email available")
    
    if lead_event.do_not_contact:
        return ImmediateSendResult(
            success=False,
            action="blocked",
            reason="Lead marked do_not_contact"
        )
    
    contact_name = lead_event.lead_name or lead_event.enriched_contact_name
    company_name = lead_event.lead_company or lead_event.enriched_company_name or "Your company"
    
    customer = None
    business_profile = None
    outreach_mode = OUTREACH_MODE_AUTO
    do_not_contact_list = None
    cc_email = None
    reply_to = None
    niche = "small business"
    city = "Miami"
    outreach_style = "transparent_ai"
    
    if lead_event.company_id:
        customer = get_customer_by_id(session, lead_event.company_id)
        if customer:
            outreach_mode = customer.outreach_mode or OUTREACH_MODE_AUTO
            outreach_style = getattr(customer, 'outreach_style', 'transparent_ai') or 'transparent_ai'
            city = customer.geography or "Miami"
            niche = customer.niche or niche
            business_profile = get_business_profile(session, customer.id)
            if business_profile:
                do_not_contact_list = business_profile.do_not_contact_list
            cc_email = customer.contact_email
            reply_to = customer.contact_email
            if business_profile and business_profile.primary_contact_email:
                cc_email = business_profile.primary_contact_email
                reply_to = business_profile.primary_contact_email
    
    if check_do_not_contact(contact_email, do_not_contact_list):
        return ImmediateSendResult(
            success=False,
            action="blocked",
            reason="Email in do_not_contact list"
        )
    
    from agents import check_rate_limits
    rate_ok, rate_reason = check_rate_limits(session, contact_email, lead_event.id, lead_event.company_id)
    if not rate_ok:
        return ImmediateSendResult(
            success=False,
            action="rate_limited",
            reason=rate_reason
        )
    
    source_url = _get_source_url_for_lead_event(session, lead_event)
    
    from agents import generate_miami_contextual_email
    subject, body = generate_miami_contextual_email(
        contact_name=contact_name or "there",
        company_name=company_name,
        niche=niche,
        event_summary=lead_event.summary,
        recommended_action=lead_event.recommended_action or "contextual outreach",
        category=lead_event.category,
        urgency_score=lead_event.urgency_score,
        outreach_style=outreach_style,
        event_id=lead_event.id,
        signal_id=lead_event.signal_id,
        city=city,
        source_url=source_url
    )
    
    if outreach_mode == OUTREACH_MODE_REVIEW and customer:
        create_pending_outbound(
            session=session,
            customer_id=customer.id,
            lead_id=lead_event.lead_id,
            to_email=contact_email,
            to_name=contact_name,
            subject=subject,
            body=body,
            context_summary=f"Signal-triggered: {lead_event.category} - {lead_event.summary[:100] if lead_event.summary else ''}",
            lead_event_id=lead_event.id
        )
        lead_event.outbound_message = body
        lead_event.next_step = "Awaiting your review"
        lead_event.next_step_owner = NEXT_STEP_OWNER_CUSTOMER
        session.add(lead_event)
        
        if commit:
            session.commit()
        
        print(f"[IMMEDIATE-SEND] Event {lead_event.id} for {company_name}: QUEUED for review (REVIEW mode)")
        return ImmediateSendResult(
            success=True,
            action="queued",
            reason="Queued for customer review (REVIEW mode)",
            queued_for_review=True
        )
    
    email_result = send_email(
        to_email=contact_email,
        subject=subject,
        body=body,
        lead_name=contact_name,
        company=company_name,
        cc_email=cc_email,
        reply_to=reply_to
    )
    
    lead_event.outbound_message = body
    lead_event.outbound_subject = subject
    
    if email_result.actually_sent or email_result.result in ("dry_run", "fallback"):
        lead_event.status = LEAD_STATUS_CONTACTED
        lead_event.enrichment_status = ENRICHMENT_STATUS_OUTBOUND_SENT
        lead_event.last_contact_at = datetime.utcnow()
        lead_event.last_contact_summary = f"Contextual email sent: {lead_event.category}"
        lead_event.next_step_owner = NEXT_STEP_OWNER_AGENT
        lead_event.contact_count_24h = (lead_event.contact_count_24h or 0) + 1
        lead_event.contact_count_7d = (lead_event.contact_count_7d or 0) + 1
        lead_event.last_subject_hash = hashlib.md5(subject.encode()).hexdigest()[:16]
        session.add(lead_event)
        
        create_pending_outbound(
            session=session,
            customer_id=customer.id if customer else None,
            lead_id=lead_event.lead_id,
            to_email=contact_email,
            to_name=contact_name,
            subject=subject,
            body=body,
            context_summary=f"Signal-triggered: {lead_event.category} - {lead_event.summary[:100] if lead_event.summary else ''}",
            lead_event_id=lead_event.id,
            status="SENT"
        )
        
        if commit:
            session.commit()
        
        mode_str = "SENT" if email_result.actually_sent else f"DRY_RUN ({email_result.mode})"
        print(f"[IMMEDIATE-SEND] Event {lead_event.id} for {company_name}: {mode_str} → OUTBOUND_SENT")
        return ImmediateSendResult(
            success=True,
            action="sent",
            reason=f"Email {mode_str.lower()}",
            email_sent=email_result.actually_sent
        )
    else:
        print(f"[IMMEDIATE-SEND] Event {lead_event.id} for {company_name}: FAILED error=\"{email_result.error}\"")
        return ImmediateSendResult(
            success=False,
            action="failed",
            reason=f"Email failed: {email_result.error}"
        )
