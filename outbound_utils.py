"""
Outbound utilities for HossAgent.
Handles business profile lookups, do-not-contact checks, and pending outbound creation.
"""
import json
from typing import Optional, List
from datetime import datetime
from sqlmodel import Session, select
from models import BusinessProfile, PendingOutbound, Customer


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
    customer_id: int,
    lead_id: Optional[int],
    to_email: str,
    to_name: Optional[str],
    subject: str,
    body: str,
    context_summary: Optional[str] = None,
    lead_event_id: Optional[int] = None
) -> PendingOutbound:
    """
    Create a PendingOutbound record for REVIEW mode.
    
    Args:
        session: Database session
        customer_id: The customer ID this outbound belongs to
        lead_id: Optional lead ID this outbound is for
        to_email: Recipient email address
        to_name: Recipient name
        subject: Email subject
        body: Email body
        context_summary: Why this email is being sent
        lead_event_id: Optional lead event ID this outbound is for
        
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
        status="PENDING",
        created_at=datetime.utcnow()
    )
    session.add(pending)
    session.flush()
    
    print(f"[OUTBOUND] Created PendingOutbound {pending.id} for customer {customer_id}: to={to_email} subject=\"{subject[:50]}...\"")
    
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
