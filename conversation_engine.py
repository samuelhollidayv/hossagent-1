"""
HossAgent Conversation Engine

The full inbound-outbound loop where strangers become leads, leads become
conversations, and conversations become revenue.

Components:
- Inbound email parsing and storage
- Thread management (message linking, status tracking)
- Draft reply generation with LLM
- Guardrails engine (pricing, scheduling, sensitive content detection)
- Suppression/opt-out handling
- Human-in-the-loop priority enforcement
- Conversation metrics tracking

Environment Variables:
- INBOUND_EMAIL_SECRET: Secret token for inbound webhook validation
- AUTO_REPLY_LEVEL: NONE | SAFE_ONLY | AGGRESSIVE (default: SAFE_ONLY)
- OPENAI_API_KEY: Required for AI draft generation
"""

import os
import re
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field, asdict
from sqlmodel import Session, select

from models import (
    Customer, Lead, LeadEvent, BusinessProfile, Thread, Message, Suppression,
    ConversationMetrics, OPT_OUT_PHRASES,
    THREAD_STATUS_OPEN, THREAD_STATUS_HUMAN_OWNED, THREAD_STATUS_AUTO, THREAD_STATUS_CLOSED,
    MESSAGE_DIRECTION_INBOUND, MESSAGE_DIRECTION_OUTBOUND,
    MESSAGE_STATUS_QUEUED, MESSAGE_STATUS_SENT, MESSAGE_STATUS_DRAFT, MESSAGE_STATUS_FAILED, MESSAGE_STATUS_APPROVED,
    MESSAGE_GENERATED_AI, MESSAGE_GENERATED_HUMAN, MESSAGE_GENERATED_SYSTEM
)
from email_utils import send_email, get_sendgrid_config, EmailResult


INBOUND_EMAIL_SECRET = os.getenv("INBOUND_EMAIL_SECRET", "")
AUTO_REPLY_LEVEL = os.getenv("AUTO_REPLY_LEVEL", "SAFE_ONLY").upper()


@dataclass
class InboundEmailData:
    """Parsed inbound email data from SendGrid webhook."""
    from_email: str
    to_email: str
    cc: Optional[str] = None
    subject: str = ""
    body_text: str = ""
    body_html: Optional[str] = None
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    received_at: datetime = field(default_factory=datetime.utcnow)
    raw_headers: Optional[Dict[str, Any]] = None


@dataclass 
class GuardrailResult:
    """Result of guardrail check on proposed reply."""
    passed: bool
    flags: List[str] = field(default_factory=list)
    details: Dict[str, str] = field(default_factory=dict)
    auto_send_allowed: bool = False


PRICING_PATTERNS = [
    r'\$\d+',
    r'\d+\s*(?:dollars|bucks)',
    r'(?:price|cost|fee|rate|quote)\s*(?:is|of|:)?\s*\$?\d+',
    r'(?:charge|bill)\s*you\s*\$?\d+',
    r'(?:discount|off|savings?)\s*(?:of)?\s*\d+%?',
    r'(?:free|no\s*(?:charge|cost))',
    r'(?:per\s*hour|hourly\s*rate)',
]

SCHEDULING_PATTERNS = [
    r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+\d+',
    r'(?:tomorrow|today)\s+at\s+\d+',
    r'\d{1,2}[:/]\d{2}\s*(?:am|pm)?',
    r'(?:schedule|book|set\s*up)\s*(?:a|an)?\s*(?:meeting|call|appointment)\s*(?:for|on)',
    r"(?:i'll|I will|we'll|we will)\s*(?:come|be there|arrive|visit)\s*(?:on|at)",
    r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}',
]

COMMITMENT_PATTERNS = [
    r"(?:i|we)\s*(?:'ll|will|can|shall)\s*(?:definitely|certainly|absolutely)\s*(?:do|provide|deliver|complete)",
    r'(?:guaranteed|guarantee|promise|committed)',
    r"(?:i|we)\s*(?:'ll|will)\s*(?:beat|match)\s*(?:their|any)\s*(?:price|quote|offer)",
    r'(?:lower|reduce|drop)\s*(?:the|our)?\s*price',
]

SENSITIVE_PATTERNS = [
    r'(?:legal|lawsuit|sue|court|attorney|lawyer)',
    r'(?:medical|diagnosis|prescription|treatment|doctor)',
    r'(?:insurance|liability|claim)',
]

SAFE_REPLY_PATTERNS = [
    r'^(?:thanks|thank you|got it|received|understood)',
    r'(?:here\'?s?\s*(?:a|some|more)?\s*(?:info|information|details|link))',
    r'(?:let me know|feel free to|please reach out)',
    r'(?:following up|checking in|just wanted to)',
]


def validate_inbound_secret(provided_secret: str) -> bool:
    """Validate inbound webhook secret token."""
    if not INBOUND_EMAIL_SECRET:
        print("[CONVERSATION][WARNING] INBOUND_EMAIL_SECRET not configured - webhook validation disabled")
        return True
    return provided_secret == INBOUND_EMAIL_SECRET


def parse_sendgrid_inbound(request_data: Dict[str, Any]) -> InboundEmailData:
    """
    Parse SendGrid Inbound Parse webhook data.
    
    SendGrid sends multipart form data with fields like:
    - from, to, cc, subject, text, html
    - headers (JSON), envelope (JSON)
    - attachments, attachment-info (if any)
    """
    from_email = request_data.get("from", "")
    if "<" in from_email and ">" in from_email:
        match = re.search(r'<([^>]+)>', from_email)
        if match:
            from_email = match.group(1)
    
    to_email = request_data.get("to", "")
    if "<" in to_email and ">" in to_email:
        match = re.search(r'<([^>]+)>', to_email)
        if match:
            to_email = match.group(1)
    
    headers_str = request_data.get("headers", "{}")
    try:
        headers = json.loads(headers_str) if isinstance(headers_str, str) else headers_str
    except json.JSONDecodeError:
        headers = {}
    
    message_id = None
    in_reply_to = None
    references = None
    
    if isinstance(headers, dict):
        message_id = headers.get("Message-ID") or headers.get("Message-Id") or headers.get("message-id")
        in_reply_to = headers.get("In-Reply-To") or headers.get("in-reply-to")
        references = headers.get("References") or headers.get("references")
    elif isinstance(headers, str):
        mid_match = re.search(r'Message-I[dD]:\s*<?([^>\s]+)>?', headers)
        if mid_match:
            message_id = mid_match.group(1)
        irt_match = re.search(r'In-Reply-To:\s*<?([^>\s]+)>?', headers)
        if irt_match:
            in_reply_to = irt_match.group(1)
    
    return InboundEmailData(
        from_email=from_email.strip().lower(),
        to_email=to_email.strip().lower(),
        cc=request_data.get("cc", ""),
        subject=request_data.get("subject", "(No Subject)"),
        body_text=request_data.get("text", ""),
        body_html=request_data.get("html"),
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        received_at=datetime.utcnow(),
        raw_headers=headers if isinstance(headers, dict) else None
    )


def find_thread_by_message_id(session: Session, in_reply_to: str) -> Optional[Thread]:
    """Find thread by matching in_reply_to to an existing message's message_id."""
    if not in_reply_to:
        return None
    
    existing_msg = session.exec(
        select(Message).where(Message.message_id == in_reply_to)
    ).first()
    
    if existing_msg and existing_msg.thread_id:
        thread = session.exec(
            select(Thread).where(Thread.id == existing_msg.thread_id)
        ).first()
        return thread
    
    return None


def find_thread_by_email(session: Session, lead_email: str, customer_id: int) -> Optional[Thread]:
    """Find existing thread by lead email and customer."""
    thread = session.exec(
        select(Thread).where(
            Thread.lead_email == lead_email.lower(),
            Thread.customer_id == customer_id,
            Thread.status != THREAD_STATUS_CLOSED
        ).order_by(Thread.updated_at.desc())
    ).first()
    return thread


def find_customer_by_email(session: Session, email: str) -> Optional[Customer]:
    """Find customer by contact email."""
    return session.exec(
        select(Customer).where(Customer.contact_email == email.lower())
    ).first()


def find_lead_event_by_email(session: Session, email: str, customer_id: int = None) -> Optional[LeadEvent]:
    """Find LeadEvent by lead email."""
    query = select(LeadEvent).where(
        (LeadEvent.lead_email == email.lower()) | 
        (LeadEvent.enriched_email == email.lower())
    )
    if customer_id:
        query = query.where(LeadEvent.company_id == customer_id)
    return session.exec(query.order_by(LeadEvent.created_at.desc())).first()


def detect_opt_out(body_text: str) -> bool:
    """Check if message body contains opt-out language."""
    body_lower = body_text.lower()
    for phrase in OPT_OUT_PHRASES:
        if phrase in body_lower:
            return True
    return False


def check_suppression(session: Session, email: str, customer_id: int = None) -> bool:
    """Check if email is suppressed for this customer or globally."""
    email_lower = email.lower()
    domain = email_lower.split("@")[1] if "@" in email_lower else ""
    
    query = select(Suppression).where(
        (Suppression.email == email_lower) |
        (Suppression.domain == domain) |
        (Suppression.is_global == True)
    )
    
    if customer_id:
        query = query.where(
            (Suppression.customer_id == customer_id) | 
            (Suppression.customer_id == None)
        )
    
    suppression = session.exec(query).first()
    return suppression is not None


def add_suppression(
    session: Session,
    email: str,
    customer_id: int,
    reason: str,
    source_message_id: int = None,
    source_thread_id: int = None,
    include_domain: bool = False
) -> Suppression:
    """Add email/domain to suppression list."""
    email_lower = email.lower()
    domain = email_lower.split("@")[1] if "@" in email_lower and include_domain else None
    
    suppression = Suppression(
        customer_id=customer_id,
        email=email_lower,
        domain=domain,
        reason=reason,
        source_message_id=source_message_id,
        source_thread_id=source_thread_id,
        is_global=False
    )
    session.add(suppression)
    session.commit()
    session.refresh(suppression)
    
    print(f"[CONVERSATION][SUPPRESSION] Added {email_lower} to suppression (reason: {reason})")
    return suppression


def create_thread(
    session: Session,
    customer_id: int,
    lead_email: str,
    lead_name: str = None,
    lead_company: str = None,
    lead_event_id: int = None,
    lead_id: int = None
) -> Thread:
    """Create a new conversation thread."""
    thread = Thread(
        customer_id=customer_id,
        lead_email=lead_email.lower(),
        lead_name=lead_name,
        lead_company=lead_company,
        lead_event_id=lead_event_id,
        lead_id=lead_id,
        status=THREAD_STATUS_OPEN,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    session.add(thread)
    session.commit()
    session.refresh(thread)
    
    print(f"[CONVERSATION][THREAD] Created thread #{thread.id} for {lead_email}")
    return thread


def store_inbound_message(
    session: Session,
    data: InboundEmailData,
    thread: Thread,
    customer_id: int = None,
    lead_event_id: int = None,
    lead_id: int = None
) -> Message:
    """Store inbound email as a Message record."""
    message = Message(
        thread_id=thread.id,
        customer_id=customer_id,
        lead_event_id=lead_event_id,
        lead_id=lead_id,
        direction=MESSAGE_DIRECTION_INBOUND,
        from_email=data.from_email,
        to_email=data.to_email,
        cc=data.cc,
        subject=data.subject,
        body_text=data.body_text,
        body_html=data.body_html,
        message_id=data.message_id,
        in_reply_to=data.in_reply_to,
        references=data.references,
        raw_metadata=json.dumps(data.raw_headers) if data.raw_headers else None,
        created_at=data.received_at
    )
    session.add(message)
    
    thread.message_count += 1
    thread.inbound_count += 1
    thread.last_message_at = data.received_at
    thread.last_direction = MESSAGE_DIRECTION_INBOUND
    thread.last_summary = data.body_text[:100] if data.body_text else data.subject[:100]
    thread.updated_at = datetime.utcnow()
    
    if thread.inbound_count == 1 and thread.outbound_count > 0:
        first_outbound = session.exec(
            select(Message).where(
                Message.thread_id == thread.id,
                Message.direction == MESSAGE_DIRECTION_OUTBOUND
            ).order_by(Message.created_at.asc())
        ).first()
        if first_outbound:
            response_time = (data.received_at - first_outbound.created_at).total_seconds()
            thread.first_response_at = data.received_at
            thread.response_time_seconds = int(response_time)
    
    session.commit()
    session.refresh(message)
    
    print(f"[CONVERSATION][MESSAGE] Stored inbound message #{message.id} in thread #{thread.id}")
    return message


def store_outbound_message(
    session: Session,
    thread: Thread,
    customer_id: int,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str = None,
    status: str = MESSAGE_STATUS_DRAFT,
    generated_by: str = MESSAGE_GENERATED_AI,
    message_id: str = None,
    guardrail_flags: List[str] = None,
    lead_event_id: int = None,
    lead_id: int = None
) -> Message:
    """Store outbound email as a Message record."""
    config = get_sendgrid_config()
    from_email = config.get("from_email", "hello@hossagent.net")
    
    customer = session.exec(select(Customer).where(Customer.id == customer_id)).first()
    reply_to = customer.contact_email if customer else config.get("reply_to")
    cc = customer.contact_email if customer else None
    
    message = Message(
        thread_id=thread.id,
        customer_id=customer_id,
        lead_event_id=lead_event_id,
        lead_id=lead_id,
        direction=MESSAGE_DIRECTION_OUTBOUND,
        from_email=from_email,
        to_email=to_email.lower(),
        cc=cc,
        reply_to=reply_to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        message_id=message_id,
        status=status,
        generated_by=generated_by,
        guardrail_flags=json.dumps(guardrail_flags) if guardrail_flags else None,
        created_at=datetime.utcnow()
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    
    print(f"[CONVERSATION][MESSAGE] Stored outbound {status} #{message.id} in thread #{thread.id}")
    return message


def check_guardrails(body_text: str) -> GuardrailResult:
    """
    Check proposed reply against guardrails.
    
    Returns GuardrailResult indicating if message passes safety checks.
    """
    flags = []
    details = {}
    body_lower = body_text.lower()
    
    for pattern in PRICING_PATTERNS:
        if re.search(pattern, body_lower):
            flags.append("pricing")
            match = re.search(pattern, body_lower)
            details["pricing"] = match.group(0) if match else "pricing language detected"
            break
    
    for pattern in SCHEDULING_PATTERNS:
        if re.search(pattern, body_lower):
            flags.append("scheduling")
            match = re.search(pattern, body_lower)
            details["scheduling"] = match.group(0) if match else "scheduling language detected"
            break
    
    for pattern in COMMITMENT_PATTERNS:
        if re.search(pattern, body_lower):
            flags.append("commitment")
            match = re.search(pattern, body_lower)
            details["commitment"] = match.group(0) if match else "commitment language detected"
            break
    
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, body_lower):
            flags.append("sensitive")
            match = re.search(pattern, body_lower)
            details["sensitive"] = match.group(0) if match else "sensitive content detected"
            break
    
    passed = len(flags) == 0
    
    auto_send_allowed = False
    if passed and AUTO_REPLY_LEVEL in ["SAFE_ONLY", "AGGRESSIVE"]:
        for pattern in SAFE_REPLY_PATTERNS:
            if re.search(pattern, body_lower):
                auto_send_allowed = True
                break
        
        if AUTO_REPLY_LEVEL == "AGGRESSIVE":
            auto_send_allowed = True
    
    return GuardrailResult(
        passed=passed,
        flags=flags,
        details=details,
        auto_send_allowed=auto_send_allowed
    )


def get_thread_context(session: Session, thread_id: int, max_messages: int = 10) -> List[Dict[str, Any]]:
    """Get recent messages from thread for context."""
    messages = session.exec(
        select(Message).where(Message.thread_id == thread_id)
        .order_by(Message.created_at.desc())
        .limit(max_messages)
    ).all()
    
    context = []
    for msg in reversed(messages):
        context.append({
            "direction": msg.direction,
            "from": msg.from_email,
            "to": msg.to_email,
            "subject": msg.subject,
            "body": msg.body_text[:500] if msg.body_text else "",
            "timestamp": msg.created_at.isoformat() if msg.created_at else None
        })
    
    return context


def get_business_profile_context(session: Session, customer_id: int) -> Dict[str, Any]:
    """Get business profile for AI context."""
    profile = session.exec(
        select(BusinessProfile).where(BusinessProfile.customer_id == customer_id)
    ).first()
    
    if not profile:
        return {}
    
    return {
        "short_description": profile.short_description,
        "services": profile.services,
        "pricing_notes": profile.pricing_notes,
        "ideal_customer": profile.ideal_customer,
        "voice_tone": profile.voice_tone or "professional",
        "communication_style": profile.communication_style or "conversational",
        "constraints": profile.constraints,
        "primary_contact_name": profile.primary_contact_name
    }


def generate_ai_draft_reply(
    session: Session,
    thread: Thread,
    inbound_message: Message,
    customer_id: int
) -> Optional[str]:
    """
    Generate AI draft reply using OpenAI.
    
    Returns generated reply text or None if generation fails.
    """
    try:
        import openai
    except ImportError:
        print("[CONVERSATION][AI] OpenAI not available - cannot generate draft")
        return None
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[CONVERSATION][AI] OPENAI_API_KEY not configured")
        return None
    
    thread_context = get_thread_context(session, thread.id)
    business_profile = get_business_profile_context(session, customer_id)
    
    customer = session.exec(select(Customer).where(Customer.id == customer_id)).first()
    customer_company = customer.company if customer else "the business"
    
    system_prompt = f"""You are a professional assistant helping {customer_company} respond to a business inquiry.

Business Context:
- Description: {business_profile.get('short_description', 'A professional service business')}
- Services: {business_profile.get('services', 'Various professional services')}
- Voice/Tone: {business_profile.get('voice_tone', 'professional and friendly')}
- Communication Style: {business_profile.get('communication_style', 'conversational but professional')}

IMPORTANT RULES:
1. DO NOT quote specific prices, rates, or discounts
2. DO NOT commit to specific dates or times for appointments
3. DO NOT make guarantees or promises
4. DO NOT provide legal, medical, or insurance advice
5. Keep responses brief and focused (2-3 short paragraphs max)
6. Suggest next steps like scheduling a call or providing more information
7. Be helpful but redirect specifics to a direct conversation

The response should:
- Acknowledge the lead's message
- Provide helpful information without overcommitting
- Encourage further conversation
- Maintain the business's voice and tone"""

    conversation_history = "\n".join([
        f"{'Lead' if msg['direction'] == 'INBOUND' else 'Us'}: {msg['body'][:300]}"
        for msg in thread_context
    ])
    
    user_prompt = f"""Conversation history:
{conversation_history}

Generate a brief, professional reply to the lead's latest message. Remember the rules above."""

    try:
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        draft = response.choices[0].message.content.strip()
        print(f"[CONVERSATION][AI] Generated draft reply ({len(draft)} chars)")
        return draft
        
    except Exception as e:
        print(f"[CONVERSATION][AI] Error generating draft: {e}")
        return None


def process_inbound_email(session: Session, data: InboundEmailData) -> Dict[str, Any]:
    """
    Process an inbound email through the Conversation Engine.
    
    Steps:
    1. Find or create thread
    2. Store message
    3. Check for opt-out
    4. Generate AI draft (if applicable)
    5. Apply guardrails
    6. Update thread status
    
    Returns processing result with thread_id, message_id, actions taken.
    """
    result = {
        "success": False,
        "thread_id": None,
        "message_id": None,
        "actions": [],
        "error": None
    }
    
    try:
        customer = None
        lead_event = None
        thread = None
        
        customer = find_customer_by_email(session, data.to_email)
        if not customer:
            cc_emails = [e.strip().lower() for e in (data.cc or "").split(",") if e.strip()]
            for cc_email in cc_emails:
                customer = find_customer_by_email(session, cc_email)
                if customer:
                    break
        
        if not customer:
            result["error"] = f"No customer found for {data.to_email}"
            print(f"[CONVERSATION][INBOUND] {result['error']}")
            return result
        
        if check_suppression(session, data.from_email, customer.id):
            result["error"] = f"Sender {data.from_email} is suppressed"
            result["actions"].append("suppressed_sender_blocked")
            print(f"[CONVERSATION][INBOUND] {result['error']}")
            return result
        
        if data.in_reply_to:
            thread = find_thread_by_message_id(session, data.in_reply_to)
            if thread:
                result["actions"].append("matched_by_message_id")
        
        if not thread:
            thread = find_thread_by_email(session, data.from_email, customer.id)
            if thread:
                result["actions"].append("matched_by_email")
        
        lead_event = find_lead_event_by_email(session, data.from_email, customer.id)
        
        if not thread:
            thread = create_thread(
                session=session,
                customer_id=customer.id,
                lead_email=data.from_email,
                lead_name=None,
                lead_company=None,
                lead_event_id=lead_event.id if lead_event else None,
                lead_id=lead_event.lead_id if lead_event else None
            )
            result["actions"].append("created_new_thread")
        
        message = store_inbound_message(
            session=session,
            data=data,
            thread=thread,
            customer_id=customer.id,
            lead_event_id=lead_event.id if lead_event else None,
            lead_id=lead_event.lead_id if lead_event else None
        )
        
        result["thread_id"] = thread.id
        result["message_id"] = message.id
        
        if detect_opt_out(data.body_text):
            add_suppression(
                session=session,
                email=data.from_email,
                customer_id=customer.id,
                reason="opt_out",
                source_message_id=message.id,
                source_thread_id=thread.id
            )
            
            thread.status = THREAD_STATUS_CLOSED
            thread.closed_reason = "opt_out"
            thread.closed_at = datetime.utcnow()
            
            if lead_event:
                lead_event.do_not_contact = True
                lead_event.do_not_contact_reason = "opt_out_reply"
                lead_event.do_not_contact_at = datetime.utcnow()
            
            session.commit()
            result["actions"].append("opt_out_processed")
            result["success"] = True
            return result
        
        if data.from_email.lower() == customer.contact_email.lower():
            if thread.status != THREAD_STATUS_HUMAN_OWNED:
                thread.status = THREAD_STATUS_HUMAN_OWNED
                session.commit()
                result["actions"].append("marked_human_owned")
            result["success"] = True
            return result
        
        if lead_event:
            lead_event.status = "RESPONDED"
            session.commit()
            result["actions"].append("lead_marked_responded")
        
        if thread.status not in [THREAD_STATUS_HUMAN_OWNED, THREAD_STATUS_CLOSED]:
            draft_text = generate_ai_draft_reply(session, thread, message, customer.id)
            
            if draft_text:
                guardrails = check_guardrails(draft_text)
                
                draft_status = MESSAGE_STATUS_DRAFT
                if guardrails.passed and guardrails.auto_send_allowed:
                    draft_status = MESSAGE_STATUS_QUEUED
                    result["actions"].append("draft_auto_approved")
                elif not guardrails.passed:
                    result["actions"].append(f"guardrails_triggered: {','.join(guardrails.flags)}")
                
                draft_message = store_outbound_message(
                    session=session,
                    thread=thread,
                    customer_id=customer.id,
                    to_email=data.from_email,
                    subject=f"Re: {data.subject}" if not data.subject.lower().startswith("re:") else data.subject,
                    body_text=draft_text,
                    status=draft_status,
                    generated_by=MESSAGE_GENERATED_AI,
                    guardrail_flags=guardrails.flags if guardrails.flags else None,
                    lead_event_id=lead_event.id if lead_event else None,
                    lead_id=lead_event.lead_id if lead_event else None
                )
                
                result["draft_message_id"] = draft_message.id
                result["actions"].append("ai_draft_created")
        
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
        print(f"[CONVERSATION][ERROR] {e}")
    
    return result


def send_queued_messages(session: Session, max_messages: int = 10) -> List[Dict[str, Any]]:
    """
    Send messages with QUEUED status.
    
    Returns list of send results.
    """
    from email_utils import send_email, EmailResult
    
    queued = session.exec(
        select(Message).where(
            Message.status == MESSAGE_STATUS_QUEUED,
            Message.direction == MESSAGE_DIRECTION_OUTBOUND
        ).order_by(Message.created_at.asc())
        .limit(max_messages)
    ).all()
    
    results = []
    
    for message in queued:
        if check_suppression(session, message.to_email, message.customer_id):
            message.status = MESSAGE_STATUS_FAILED
            message.raw_metadata = json.dumps({"error": "suppressed"})
            session.commit()
            results.append({"message_id": message.id, "status": "suppressed"})
            continue
        
        customer = session.exec(
            select(Customer).where(Customer.id == message.customer_id)
        ).first()
        
        cc_email = customer.contact_email if customer else None
        reply_to = customer.contact_email if customer else None
        
        email_result: EmailResult = send_email(
            to_email=message.to_email,
            subject=message.subject,
            body=message.body_text,
            lead_name="",
            company="",
            cc_email=cc_email,
            reply_to=reply_to
        )
        
        if email_result.success:
            message.status = MESSAGE_STATUS_SENT
            message.sent_at = datetime.utcnow()
            if email_result.sendgrid_response:
                message.sendgrid_message_id = email_result.sendgrid_response.get("x_message_id")
            
            if message.thread_id:
                thread = session.exec(
                    select(Thread).where(Thread.id == message.thread_id)
                ).first()
                if thread:
                    thread.message_count += 1
                    thread.outbound_count += 1
                    thread.last_message_at = datetime.utcnow()
                    thread.last_direction = MESSAGE_DIRECTION_OUTBOUND
                    thread.last_summary = message.body_text[:100] if message.body_text else message.subject[:100]
                    thread.updated_at = datetime.utcnow()
            
            results.append({"message_id": message.id, "status": "sent"})
        else:
            message.status = MESSAGE_STATUS_FAILED
            message.raw_metadata = json.dumps({"error": email_result.error})
            results.append({"message_id": message.id, "status": "failed", "error": email_result.error})
        
        session.commit()
    
    return results


def approve_draft(session: Session, message_id: int, approved_by: str = "customer") -> bool:
    """Approve a draft message for sending."""
    message = session.exec(
        select(Message).where(
            Message.id == message_id,
            Message.status == MESSAGE_STATUS_DRAFT
        )
    ).first()
    
    if not message:
        return False
    
    message.status = MESSAGE_STATUS_APPROVED
    message.approved_at = datetime.utcnow()
    message.approved_by = approved_by
    message.guardrail_approved = True
    session.commit()
    
    message.status = MESSAGE_STATUS_QUEUED
    session.commit()
    
    print(f"[CONVERSATION] Approved draft #{message_id}")
    return True


def edit_and_approve_draft(
    session: Session,
    message_id: int,
    new_body_text: str,
    new_subject: str = None,
    approved_by: str = "customer"
) -> bool:
    """Edit a draft message and approve for sending."""
    message = session.exec(
        select(Message).where(
            Message.id == message_id,
            Message.status == MESSAGE_STATUS_DRAFT
        )
    ).first()
    
    if not message:
        return False
    
    message.body_text = new_body_text
    if new_subject:
        message.subject = new_subject
    message.generated_by = MESSAGE_GENERATED_HUMAN
    message.status = MESSAGE_STATUS_APPROVED
    message.approved_at = datetime.utcnow()
    message.approved_by = approved_by
    message.guardrail_approved = True
    session.commit()
    
    message.status = MESSAGE_STATUS_QUEUED
    session.commit()
    
    print(f"[CONVERSATION] Edited and approved draft #{message_id}")
    return True


def discard_draft(session: Session, message_id: int) -> bool:
    """Discard a draft message."""
    message = session.exec(
        select(Message).where(
            Message.id == message_id,
            Message.status == MESSAGE_STATUS_DRAFT
        )
    ).first()
    
    if not message:
        return False
    
    session.delete(message)
    session.commit()
    
    print(f"[CONVERSATION] Discarded draft #{message_id}")
    return True


def set_thread_status(session: Session, thread_id: int, status: str) -> bool:
    """Update thread status (OPEN, HUMAN_OWNED, AUTO, CLOSED)."""
    thread = session.exec(select(Thread).where(Thread.id == thread_id)).first()
    if not thread:
        return False
    
    thread.status = status
    thread.updated_at = datetime.utcnow()
    
    if status == THREAD_STATUS_CLOSED:
        thread.closed_at = datetime.utcnow()
    
    session.commit()
    print(f"[CONVERSATION] Thread #{thread_id} status -> {status}")
    return True


def calculate_customer_metrics(session: Session, customer_id: int) -> ConversationMetrics:
    """Calculate and update conversation metrics for a customer."""
    metrics = session.exec(
        select(ConversationMetrics).where(ConversationMetrics.customer_id == customer_id)
    ).first()
    
    if not metrics:
        metrics = ConversationMetrics(customer_id=customer_id)
        session.add(metrics)
    
    lead_events_count = session.exec(
        select(func.count(LeadEvent.id)).where(LeadEvent.company_id == customer_id)
    ).one()
    metrics.total_lead_events = lead_events_count
    
    threads = session.exec(
        select(Thread).where(Thread.customer_id == customer_id)
    ).all()
    metrics.total_threads = len(threads)
    
    threads_with_outbound = [t for t in threads if t.outbound_count > 0]
    threads_with_inbound = [t for t in threads if t.inbound_count > 0]
    
    metrics.leads_contacted = len(threads_with_outbound)
    metrics.leads_replied = len(threads_with_inbound)
    metrics.reply_rate_pct = (
        (metrics.leads_replied / metrics.leads_contacted * 100)
        if metrics.leads_contacted > 0 else 0.0
    )
    
    response_times = [t.response_time_seconds for t in threads if t.response_time_seconds]
    metrics.avg_response_time_seconds = (
        int(sum(response_times) / len(response_times))
        if response_times else None
    )
    
    outbound_count = session.exec(
        select(func.count(Message.id)).where(
            Message.customer_id == customer_id,
            Message.direction == MESSAGE_DIRECTION_OUTBOUND
        )
    ).one()
    inbound_count = session.exec(
        select(func.count(Message.id)).where(
            Message.customer_id == customer_id,
            Message.direction == MESSAGE_DIRECTION_INBOUND
        )
    ).one()
    metrics.total_outbound = outbound_count
    metrics.total_inbound = inbound_count
    
    ai_drafted = session.exec(
        select(func.count(Message.id)).where(
            Message.customer_id == customer_id,
            Message.generated_by == MESSAGE_GENERATED_AI
        )
    ).one()
    human_sent = session.exec(
        select(func.count(Message.id)).where(
            Message.customer_id == customer_id,
            Message.generated_by == MESSAGE_GENERATED_HUMAN
        )
    ).one()
    metrics.messages_ai_drafted = ai_drafted
    metrics.messages_human_sent = human_sent
    
    metrics.threads_human_owned = len([t for t in threads if t.status == THREAD_STATUS_HUMAN_OWNED])
    metrics.threads_closed_opt_out = len([t for t in threads if t.closed_reason == "opt_out"])
    
    total_depth = sum(t.message_count for t in threads)
    metrics.avg_thread_depth = total_depth / len(threads) if threads else 0.0
    
    metrics.last_calculated_at = datetime.utcnow()
    session.commit()
    
    return metrics


def get_thread_summary(session: Session, thread_id: int) -> Optional[Dict[str, Any]]:
    """Get summary of a thread for display."""
    thread = session.exec(select(Thread).where(Thread.id == thread_id)).first()
    if not thread:
        return None
    
    messages = session.exec(
        select(Message).where(Message.thread_id == thread_id)
        .order_by(Message.created_at.asc())
    ).all()
    
    drafts = [m for m in messages if m.status == MESSAGE_STATUS_DRAFT]
    
    return {
        "id": thread.id,
        "lead_email": thread.lead_email,
        "lead_name": thread.lead_name,
        "lead_company": thread.lead_company,
        "status": thread.status,
        "message_count": thread.message_count,
        "inbound_count": thread.inbound_count,
        "outbound_count": thread.outbound_count,
        "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
        "last_direction": thread.last_direction,
        "last_summary": thread.last_summary,
        "has_drafts": len(drafts) > 0,
        "draft_count": len(drafts),
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "messages": [
            {
                "id": m.id,
                "direction": m.direction,
                "from_email": m.from_email,
                "to_email": m.to_email,
                "subject": m.subject,
                "body_text": m.body_text,
                "status": m.status,
                "generated_by": m.generated_by,
                "guardrail_flags": json.loads(m.guardrail_flags) if m.guardrail_flags else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "sent_at": m.sent_at.isoformat() if m.sent_at else None
            }
            for m in messages
        ]
    }


def get_customer_threads(
    session: Session,
    customer_id: int,
    status_filter: str = None,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Get threads for a customer with optional status filter."""
    query = select(Thread).where(Thread.customer_id == customer_id)
    
    if status_filter:
        query = query.where(Thread.status == status_filter)
    
    query = query.order_by(Thread.updated_at.desc()).limit(limit)
    threads = session.exec(query).all()
    
    result = []
    for thread in threads:
        draft_count = session.exec(
            select(func.count(Message.id)).where(
                Message.thread_id == thread.id,
                Message.status == MESSAGE_STATUS_DRAFT
            )
        ).one()
        
        result.append({
            "id": thread.id,
            "lead_email": thread.lead_email,
            "lead_name": thread.lead_name,
            "lead_company": thread.lead_company,
            "status": thread.status,
            "message_count": thread.message_count,
            "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
            "last_direction": thread.last_direction,
            "last_summary": thread.last_summary,
            "has_drafts": draft_count > 0,
            "draft_count": draft_count
        })
    
    return result
