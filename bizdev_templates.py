"""
BizDev Template Engine for HossAgent.
Provides niche-tuned email template packs for outbound prospecting.

Environment Variables:
  BIZDEV_NICHE_TEMPLATE - Template pack to use (default: "general")
  BIZDEV_SENDER_NAME - Sender name in emails (default: "HossAgent")
  BIZDEV_SENDER_EMAIL - Sender email for replies (optional)
  BIZDEV_OFFER - Current offer/service description (optional)
"""
import os
import random
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass, asdict


@dataclass
class GeneratedEmail:
    subject: str
    body: str
    template_pack: str
    template_index: int
    placeholders_used: Dict[str, str]


TEMPLATE_LOG_FILE = Path("bizdev_template_log.json")
MAX_TEMPLATE_LOG_ENTRIES = 5000


TEMPLATE_PACKS: Dict[str, Dict[str, List[str]]] = {
    "general": {
        "subject_lines": [
            "Quick idea for {{company_name}}",
            "Taking the grunt work off your plate",
            "You + 1 autonomous ops brain",
            "Quick idea to de-risk your pipeline",
            "{{company_name}} - one less thing to worry about"
        ],
        "body_templates": [
            """Hi {{first_name}},

I've been looking at small shops like {{company_name}} that are doing solid work but still relying on a mess of spreadsheets, email threads, and late-night invoicing to keep cash coming in.

I built something for that: an autonomous "back office" that does three things on repeat:
- Finds and contacts qualified leads for you
- Tracks what work is being done for whom
- Generates invoices and shows you, in one dashboard, where the money is

It's not a CRM and it's not an agency. Think of it as a self-driving ops assistant that only cares about two things: pipeline and cash.

If you gave it one current offer (e.g., how you usually package {{niche}} work), it could start running a small, controlled experiment for you this month.

Would you be open to a 15-minute call so I can show you what that looks like with real numbers from your world?

- {{sender_name}}""",
            """Hi {{first_name}},

Running a {{niche}} business means you're juggling a lot - client work, new leads, invoicing, follow-ups. What if half of that ran itself?

I've built an autonomous system that handles the back-office grind:
- Prospecting and outreach on autopilot
- Task tracking with profit calculations
- Invoice generation when work is done

No hiring. No learning new tools. Just set your offer and let it run.

Want to see how it would work for {{company_name}}?

- {{sender_name}}""",
            """{{first_name}},

Quick question: how much time do you spend each week on admin work that isn't billable?

For most {{niche}} folks I talk to, it's 10-15 hours. That's $2K-$5K in lost revenue every month.

I built a system that handles leads, tasks, and invoicing automatically - so you can focus on the work that actually pays.

Worth a quick look?

- {{sender_name}}"""
        ]
    },
    "agency": {
        "subject_lines": [
            "Your agency's invisible back office",
            "Stop losing deals to slow follow-up",
            "{{company_name}} - what if client ops ran itself?",
            "The agency owner's leverage play",
            "Quick idea for {{company_name}}"
        ],
        "body_templates": [
            """Hi {{first_name}},

Agency life: you're great at the creative work, but the pipeline management, client onboarding, and invoicing? That's where things fall through the cracks.

I built an autonomous system specifically for this:
- Finds leads that match your ideal client profile
- Sends personalized outreach (you approve the templates)
- Tracks projects and auto-generates invoices

It's like having a silent ops partner who never sleeps and never forgets to follow up.

Mind if I show you how it would work for {{company_name}}?

- {{sender_name}}""",
            """{{first_name}},

Most agency owners I know are stuck in the feast-or-famine cycle because they only prospect when they're desperate.

What if your pipeline stayed full without you thinking about it?

I've built an autonomous engine that handles lead gen, outreach, and follow-ups on autopilot - while you focus on client delivery.

{{company_name}} seems like a good fit. Want to take a look?

- {{sender_name}}"""
        ]
    },
    "saas": {
        "subject_lines": [
            "{{company_name}} - ops that scale with you",
            "Your SaaS deserves automated back-office",
            "Quick automation idea for {{company_name}}",
            "From manual to autonomous operations"
        ],
        "body_templates": [
            """Hi {{first_name}},

Building a SaaS is hard enough without manually chasing leads and invoicing customers.

I've built an autonomous system that handles the operational side:
- Lead discovery and qualification
- Automated outreach sequences
- Revenue tracking and invoice generation

It integrates with what you already use and runs 24/7.

Worth 15 minutes to see if it fits {{company_name}}?

- {{sender_name}}""",
            """{{first_name}},

When you're scaling a SaaS, every hour spent on admin work is an hour not spent on product or customers.

My system automates the repetitive stuff:
- Finding and contacting potential customers
- Tracking deals and tasks
- Billing and revenue reporting

It's designed to run autonomously - you set the parameters, it does the work.

Interested in a quick demo for {{company_name}}?

- {{sender_name}}"""
        ]
    },
    "consulting": {
        "subject_lines": [
            "{{company_name}} - your invisible associate",
            "Consultants who hate admin work, read this",
            "Quick idea for {{first_name}}",
            "What if your practice ran itself?"
        ],
        "body_templates": [
            """Hi {{first_name}},

Most consultants I know are brilliant at their craft but drowning in the business side - finding clients, sending proposals, tracking hours, chasing payments.

I built an autonomous system that handles all of that:
- Finds and reaches out to potential clients
- Tracks engagements and deliverables
- Generates invoices automatically

You focus on the consulting. The system handles the business of consulting.

Would you be open to a quick walkthrough for {{company_name}}?

- {{sender_name}}""",
            """{{first_name}},

Here's the consulting paradox: the more successful you get, the less time you have to find new clients.

My system breaks that cycle by automating your pipeline:
- Identifies prospects in your niche
- Sends personalized outreach
- Tracks responses and schedules follow-ups

It's like having a full-time business development person, minus the salary.

Interested?

- {{sender_name}}"""
        ]
    },
    "revops": {
        "subject_lines": [
            "{{company_name}} - revenue on autopilot",
            "Your RevOps engine, fully autonomous",
            "Quick revenue idea for {{first_name}}",
            "From RevOps to RevAuto"
        ],
        "body_templates": [
            """Hi {{first_name}},

You know better than most: revenue operations is about removing friction from the money flow.

I built an autonomous system that does exactly that:
- Pipeline generation (finds and contacts leads automatically)
- Work tracking with cost/profit analysis
- Automated invoicing and revenue reporting

No more spreadsheet gymnastics. No more manual follow-ups.

Worth a look for {{company_name}}?

- {{sender_name}}""",
            """{{first_name}},

What if your entire revenue operation - from lead to invoice - ran autonomously?

That's what I built:
- Lead sourcing and outreach on autopilot
- Task and project management with real-time P&L
- Invoice generation triggered by work completion

It's RevOps without the ops.

Mind if I show you how it would work for {{company_name}}?

- {{sender_name}}"""
        ]
    }
}


def get_template_pack_name() -> str:
    """Get the configured template pack name from environment."""
    return os.getenv("BIZDEV_NICHE_TEMPLATE", "general").lower()


def get_sender_name() -> str:
    """Get sender name from environment."""
    return os.getenv("BIZDEV_SENDER_NAME", "HossAgent")


def get_sender_email() -> Optional[str]:
    """Get sender email from environment (optional)."""
    return os.getenv("BIZDEV_SENDER_EMAIL")


def get_offer_description() -> str:
    """Get current offer description from environment."""
    return os.getenv("BIZDEV_OFFER", "autonomous business operations")


def get_dashboard_url() -> str:
    """Get dashboard URL from environment."""
    base = os.getenv("REPLIT_DEV_DOMAIN", "")
    if base:
        return f"https://{base}"
    return os.getenv("DASHBOARD_URL", "")


def list_template_packs() -> List[str]:
    """List all available template pack names."""
    return list(TEMPLATE_PACKS.keys())


def get_template_pack(name: str) -> Optional[Dict[str, List[str]]]:
    """Get a specific template pack by name."""
    return TEMPLATE_PACKS.get(name.lower())


def _load_template_log() -> List[Dict[str, Any]]:
    """Load template generation log."""
    try:
        if TEMPLATE_LOG_FILE.exists():
            with open(TEMPLATE_LOG_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_template_log(entries: List[Dict[str, Any]]) -> None:
    """Save template generation log."""
    try:
        entries = entries[-MAX_TEMPLATE_LOG_ENTRIES:]
        with open(TEMPLATE_LOG_FILE, "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        print(f"[BIZDEV] Warning: Could not save template log: {e}")


def log_template_generation(email: GeneratedEmail, lead_id: int, lead_email: str) -> None:
    """Log a generated email for admin visibility."""
    entries = _load_template_log()
    entries.append({
        "timestamp": datetime.utcnow().isoformat(),
        "lead_id": lead_id,
        "lead_email": lead_email,
        "template_pack": email.template_pack,
        "template_index": email.template_index,
        "subject": email.subject,
        "body_preview": email.body[:200] + "..." if len(email.body) > 200 else email.body
    })
    _save_template_log(entries)


def get_template_log(limit: int = 10) -> List[Dict[str, Any]]:
    """Get recent template generations for admin display."""
    entries = _load_template_log()
    return entries[-limit:]


def generate_email(
    first_name: str,
    company_name: str,
    niche: str = "",
    email: str = "",
    industry: str = ""
) -> GeneratedEmail:
    """
    Generate a personalized email using the configured template pack.
    
    Args:
        first_name: Contact's first name
        company_name: Company name
        niche: Lead's niche/industry (optional)
        email: Lead's email (for logging)
        industry: Industry category (optional)
    
    Returns:
        GeneratedEmail with filled-in subject and body
    """
    pack_name = get_template_pack_name()
    pack = get_template_pack(pack_name)
    
    if not pack:
        print(f"[BIZDEV][TEMPLATE] Pack '{pack_name}' not found, using 'general'")
        pack_name = "general"
        pack = TEMPLATE_PACKS["general"]
    
    subject_template = random.choice(pack["subject_lines"])
    body_index = random.randint(0, len(pack["body_templates"]) - 1)
    body_template = pack["body_templates"][body_index]
    
    placeholders = {
        "first_name": first_name or "there",
        "company_name": company_name or "your company",
        "niche": niche or industry or "your industry",
        "industry": industry or niche or "your industry",
        "offer": get_offer_description(),
        "sender_name": get_sender_name(),
        "sender_email": get_sender_email() or "",
        "dashboard_url": get_dashboard_url()
    }
    
    subject = subject_template
    body = body_template
    
    for key, value in placeholders.items():
        subject = subject.replace(f"{{{{{key}}}}}", value)
        body = body.replace(f"{{{{{key}}}}}", value)
    
    return GeneratedEmail(
        subject=subject,
        body=body,
        template_pack=pack_name,
        template_index=body_index,
        placeholders_used=placeholders
    )


def get_template_status() -> Dict[str, Any]:
    """Get current template configuration status for admin display."""
    pack_name = get_template_pack_name()
    pack = get_template_pack(pack_name)
    
    return {
        "active_pack": pack_name,
        "pack_exists": pack is not None,
        "available_packs": list_template_packs(),
        "sender_name": get_sender_name(),
        "sender_email": get_sender_email(),
        "offer": get_offer_description(),
        "subject_count": len(pack["subject_lines"]) if pack else 0,
        "body_count": len(pack["body_templates"]) if pack else 0
    }
