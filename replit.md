# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system with a noir aesthetic, designed to autonomously identify leads, convert them into customers, execute tasks, and generate Stripe-powered invoices while tracking real-time profit. It orchestrates four specialized AI agents (BizDev, Onboarding, Ops, Billing). The system offers both customer-facing and administrative interfaces with robust authentication. The business model is a $99/month SaaS subscription, including a 7-day free trial with restricted access (15 tasks, 20 leads max). The project aims to provide a comprehensive, autonomous business solution, initially targeting the South Florida market with ambitions for broader application.

## User Preferences
Not specified.

## System Architecture
HossAgent is built on a FastAPI backend, utilizing SQLModel for data persistence with PostgreSQL (production) or SQLite (development fallback). The system's core functionality revolves around asynchronous, idempotent agent cycles.

**UI/UX Design (2025 Professional Noir):**
- **Fonts**: Inter (customer-facing) and JetBrains Mono (admin console).
- **Color System (CSS Variables)**: Deep black (`--bg-primary: #0a0a0a`), elevated surfaces (`--bg-secondary: #111111`), subtle borders (`--border-subtle: #1f1f1f`), white primary text (`--text-primary: #ffffff`), and accent green (`--accent-green: #22c55e`).
- **Design Patterns**: Modern rounded corners (6-12px), smooth transitions, hover effects, and backdrop blur.
- **Aesthetic**: No gradients or emojis for a clean, professional appearance.

**Interfaces:**
- **Marketing Landing Page (`/`)**: Public homepage.
- **About Page (`/about`)**: Explains the "Ethical Briefcase System" concept.
- **How It Works Page (`/how-it-works`)**: 10-step user journey.
- **Admin Console (`/admin`)**: Consolidated operator dashboard with 4 clean tables:
  1. **Signals**: Raw signals from SignalNet with source, geography, score, and event creation status.
  2. **LeadEvents**: Converted opportunities with customer, lead company, email, category, and status.
  3. **Outbound Messages**: All sent emails with customer, recipient, subject, and status.
  4. **Customers**: Customer accounts with plan, status, autopilot, outreach mode, and usage limits.
- **Customer Portal (`/portal`)**: Session-authenticated portal with clean 4-section layout:
  1. **Account Status Card**: Plan name, status pill, autopilot indicator, billing info, and CTA buttons (upgrade/manage/cancel/reactivate).
  2. **Recent Opportunities & Outreach**: Combined view showing opportunities with expandable email details (to/subject/body). REVIEW mode shows approval buttons for pending outreach.
  3. **Conversations**: Email threads with leads showing message history, AI-generated drafts with Approve/Discard buttons, and thread status (Open, Your Turn, Auto, Draft Ready).
  4. **Reports & Deep Dives**: Clickable report cards with expandable content.
- **Customer Portal - Admin View (`/portal/<token>`)**: Token-based access for admin impersonation.
- **Customer Settings (`/portal/settings`)**: Business profile configuration, outreach preferences, and do-not-contact list management.

**Authentication System:**
- **Customer Authentication:** Email + password (bcrypt), 14-day session-based with HTTP-only cookies, password reset.
- **Admin Authentication:** Password gate via `ADMIN_PASSWORD` environment variable.
- **Trial Abuse Prevention:** Tracks email hash, IP, and user-agent fingerprints, with a 90-day cooldown and password requirements.

**Contextual Opportunity Engine:**
- **Outreach Modes:** AUTO (immediate sending) and REVIEW (customer approval required).
- **Business Profile:** `BusinessProfile` model stores customer preferences (services, ideal customer, voice/tone, do-not-contact lists).
- **Pending Outreach:** `PendingOutbound` queues emails for customer approval in REVIEW mode.
- **Do-Not-Contact Enforcement:** Checks email addresses and domain patterns before sending.
- **Outbound Email Direction:** To lead email, CC customer email, Reply-To customer email.
- **LeadEvent Identity Fields:** `lead_name`, `lead_email`, `lead_company`, `lead_domain`.

**Outbound Email System:**
- **Subject Line Library**: 12 variants rotated per event/signal.
- **Template Styles**: Customer-configurable (`transparent_ai` or `classic`).
- **Name Parsing**: Extracts first name only.
- **Rate Limiting**: Per-lead (daily/weekly) and per-customer limits.
- **Suppression Flow**: `do_not_contact` flag, `OPT_OUT_PHRASES` detection in replies, `check_opt_out()` and `mark_do_not_contact()` functions.
- **Email Content**: Includes opt-out instructions and website URL.

**Core Features:**
- **Autonomous Agents:** SignalNet, BizDev, Onboarding, Ops, and Billing cycles.
- **SignalNet System:** 24/7 autonomous signal ingestion network with a pluggable `SignalSource` framework.
  - **Signal Scoring (0-100)**: Weighted composite of category urgency, recency, geography, and niche match.
  - **LeadEvent Generation**: Signals scoring >= 65 create LeadEvents.
  - **`SIGNAL_MODE`**: `PRODUCTION`, `SANDBOX`, `OFF`.
  - **Admin Panel**: Provides SignalNet status, controls, and recent signal stream.
- **Reports System:** Auto-generated from completed tasks.
- **Data Models:** Comprehensive models for system settings, leads, customers, tasks, invoices, and more.
- **Email Infrastructure:** Supports multiple providers with CC/Reply-To and DRY_RUN mode.
  - **SendGrid**: Primary email provider with full feature support (outbound + inbound webhooks).
  - **Amazon SES**: Alternative email provider via boto3 (outbound only).
  - **Email Routing**: TO: lead email, CC: customer email, REPLY-TO: customer email.
  - **Configuration**: `EMAIL_MODE` environment variable (`DRY_RUN`, `SENDGRID`, `SES`).
- **HossNative Lead Discovery:** Fully autonomous lead generation system.
  - **SignalNet Integration**: Detects business signals from news, reviews, and market events.
  - **Web Scraping**: Scrapes company homepages and contact pages to find email addresses.
  - **Domain Resolution**: Extracts company names from news headlines and guesses domains.
  - **Email Validation**: Validates emails and filters invalid patterns.
  - **No External APIs**: Fully autonomous - no paid enrichment APIs required.
  - **ACTIVE_PROVIDERS**: `["HossNative"]` - the ONLY lead source.
- **OPERATION ARCHANGEL: Multi-Layered Enrichment Engine** (v2 - ACTIVE)
  - **Company Name Extraction**: Parses signal summary for quoted names and capitalized phrases, strips location markers.
  - **Domain Discovery (Multi-Layered)**:
    - Layer 1: Use existing lead_domain/lead_email fields
    - Layer 2: Parse source URL for company websites
    - Layer 3: Fetch articles and extract outbound links (blocks social/news/directories)
    - Layer 4: Web search fallback with domain guessing and verification
  - **Email Classification & Scoring**:
    - Classifies emails as: generic (info@, contact@), person-like (firstname.lastname@), or other
    - Confidence scoring factors: domain match (30%), email pattern (40% for person-like), page context (10%), TLD priority
    - Scores range 0-1.0 with person-like emails prioritized
  - **Confidence Scoring System**:
    - `domain_confidence`: 0-1.0 score reflecting domain match quality
    - `email_confidence`: 0-1.0 score reflecting email validity and context
    - `company_name_candidate`: Extracted company name for matching validation
  - **State Machine with Immediate-Send (v3 - ACTIVE)**:
    - UNENRICHED → WITH_DOMAIN_NO_EMAIL (with domain_confidence)
    - WITH_DOMAIN_NO_EMAIL → ENRICHED_NO_OUTBOUND (with email_confidence)
    - **IMMEDIATE SEND**: Email found triggers instant send (AUTO) or queue (REVIEW) - no waiting for BizDev cycle
    - ENRICHED_NO_OUTBOUND → OUTBOUND_SENT (via `send_lead_event_immediate()` in outbound_utils.py)
    - ARCHIVED for stale leads (30+ days without progress)
  - **Immediate-Send Architecture**: Eliminates 15-minute delay between enrichment and contact
    - `send_lead_event_immediate()`: Centralized helper in outbound_utils.py
    - Called directly from enrichment pipeline when email discovered
    - Handles AUTO mode (send), REVIEW mode (queue), rate limits, do-not-contact
    - BizDev cycle acts as catch-up mechanism for edge cases
  - **Admin Console Filters**: Tab-based filtering with enrichment status and confidence metrics.
  - **Customer Portal**: Shows only high-confidence OUTBOUND_SENT leads (ENRICHED_NO_OUTBOUND visible in REVIEW mode).
  - **No Paid APIs**: HossNative only - no Apollo, Hunter, or Clearbit APIs.
- **Autopilot:** Automates agent cycles every 15 minutes for paid plans.

**Conversation Engine:**
- Handles inbound email replies and AI-assisted draft generation.
- **Data Models:** `Thread`, `Message`, `Suppression`, `ConversationMetrics`.
- **State Machine:** OPEN → HUMAN_OWNED → AUTO → CLOSED.
- **Inbound Email Handling:** Receives SendGrid webhooks, creates/matches threads, detects opt-out, generates AI drafts.
- **Guardrails Engine:** Detects sensitive content in AI drafts (pricing, legal, medical) and flags for human review.
- **Human-in-the-Loop:** Draft approval workflow in Customer Portal (Approve, Edit, Discard).
- **Suppression System:** OPT_OUT_PHRASES detection, customer-level and global suppression lists.
- **MagicBox Personality Framework:** AI drafts adhere to defined voice/tone.

**Subscription Model:**
- **Trial Plan (7 days):** Soft caps (15 tasks, 20 leads), DRY_RUN email mode.
- **Paid Plan ($99/month):** Full access, unlimited tasks/leads, real email sending, full billing/autopilot.
- **Customer Flows:** Signup, upgrade via Stripe Checkout, cancellation.

## External Dependencies
- **FastAPI**: Backend web framework.
- **SQLModel**: ORM for data modeling.
- **PostgreSQL**: Production database.
- **bcrypt**: Password hashing.
- **SendGrid**: Email service provider (primary).
- **Amazon SES**: Alternative email provider (secondary).
- **Stripe**: Payment gateway for subscriptions and billing.
- **OpenWeatherMap**: Weather alerts for SignalNet.
