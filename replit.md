# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system designed with a noir aesthetic. It orchestrates four AI agents (BizDev, Onboarding, Ops, Billing) to autonomously identify leads, convert them into customers, execute tasks, and generate Stripe-powered invoices, all while tracking real-time profit. The system provides both customer-facing and administrative interfaces, complete with robust authentication. The business model is a $99/month SaaS subscription, offering a 7-day free trial with restricted access (15 tasks, 20 leads max). The project aims to provide a comprehensive, autonomous business solution, initially tailored for the South Florida market, with ambitions for broader application.

## User Preferences
Not specified.

## System Architecture
HossAgent is built on a FastAPI backend, utilizing `SQLModel` for data persistence with PostgreSQL (production) or SQLite (development fallback), ensuring schema auto-creation and migration. The system's core functionality revolves around asynchronous, idempotent agent cycles.

**UI/UX Design (2025 Professional Noir):** The system employs a modern professional noir aesthetic with:
- **Fonts**: Inter (customer-facing pages) and JetBrains Mono (admin console) via Google Fonts
- **Color System (CSS Variables)**:
  - `--bg-primary: #0a0a0a` - Deep black background
  - `--bg-secondary: #111111` - Elevated surfaces
  - `--bg-tertiary: #1a1a1a` - Tertiary backgrounds
  - `--border-subtle: #1f1f1f` - Subtle borders
  - `--border-medium: #2a2a2a` - Medium borders
  - `--text-primary: #ffffff` - Primary text
  - `--text-secondary: #a0a0a0` - Secondary text
  - `--text-tertiary: #666666` - Muted text
  - `--accent-green: #22c55e` - Primary accent
  - `--accent-green-dim: rgba(34, 197, 94, 0.15)` - Subtle green backgrounds
- **Design Patterns**: Modern rounded corners (6-12px), smooth transitions (0.2s ease), hover effects with translateY transforms, backdrop blur for elevated elements
- **Status Badges**: Pill-shaped with semi-transparent backgrounds matching accent colors
- **No Gradients or Emojis**: Maintains clean, professional appearance

**Interfaces:**
- **Marketing Landing Page (`/`)**: Public homepage with CTAs for trial and demo.
- **About Page (`/about`)**: Product philosophy and vision - explains the "Ethical Briefcase System" concept.
- **How It Works Page (`/how-it-works`)**: 10-step field manual for operators showing the complete user journey.
- **Admin Console (`/admin`)**: Consolidated operator dashboard with:
  - **KPI Bar**: Real-time metrics (Signals Today, Lead Events Today, Outbound Sent, Reports Delivered, Errors/Failed)
  - **Lead Events Table (PRIMARY)**: Shows status, has_outbound, has_report flags, urgency, company
  - **Output History**: Combined Outbound messages and Reports with tab navigation
  - **Signals Table (collapsible)**: Raw signals for debugging
  - **Customers Table**: With portal links and upgrade buttons
  - **Pending Outreach**: Cross-customer visibility of queued emails
- **Customer Portal (`/portal`)**: Session-authenticated portal displaying plan/billing status, opportunities, reports, pending outreach (REVIEW mode), invoices, and subscription management.
- **Customer Portal - Admin View (`/portal/<token>`)**: Token-based access for admin impersonation.
- **Customer Settings (`/portal/settings`)**: Business profile configuration, outreach preferences, and do-not-contact list management.

**Authentication System:**
- **Customer Authentication:** Email + password (bcrypt), 14-day session-based with HTTP-only cookies, password reset via email token (1-hour expiry).
- **Admin Authentication:** Simple password gate via `ADMIN_PASSWORD` environment variable, separate session cookie.
- **Trial Abuse Prevention:** Tracks email hash, IP, and user-agent fingerprints in `TrialIdentity` table, enforcing a 90-day cooldown and minimum 8-character passwords.

**Contextual Opportunity Engine:**
- **Outreach Modes:** AUTO (immediate sending) and REVIEW (customer approval required) modes, configurable by the customer.
- **Business Profile:** `BusinessProfile` model stores detailed customer preferences including services, ideal customer, voice/tone, contact details, and do-not-contact lists.
- **Pending Outreach:** `PendingOutbound` model queues emails for customer approval in REVIEW mode, displayed in the portal with Approve/Edit/Skip options.
- **Do-Not-Contact Enforcement:** Checks against email addresses and domain patterns before any email is sent.
- **Outbound Email Direction (Critical):**
  - **TO**: Lead email (LeadEvent.lead_email or enriched_email) - the prospect
  - **CC**: Customer email - for visibility and audit trail
  - **Reply-To**: Customer email - prospects reply directly to customer
  - Events without lead_email are skipped (never fall back to customer as recipient)
  - Self-signal detection prevents creating LeadEvents for customer's own company
- **LeadEvent Identity Fields:**
  - lead_name: Name of the lead/prospect
  - lead_email: Email of the lead (required for outbound)
  - lead_company: Company name of the lead
  - lead_domain: Domain for enrichment lookup

**Core Features:**
- **Autonomous Agents:**
    - **Signals Agent (SignalNet):** 24/7 signal ingestion network that monitors real-world business signals from multiple sources.
    - **BizDev Cycle:** Prospects new leads via email, adhering to outreach modes and Miami-tuned templates.
    - **Onboarding Cycle:** Converts qualified leads and initiates tasks.
    - **Ops Cycle:** Executes tasks, calculates reward/cost/profit, and auto-generates reports.
    - **Billing Cycle:** Generates invoices and integrates with Stripe.

**SignalNet System:** 24/7 autonomous signal ingestion network that detects real-world business context.
- **Architecture**: Pluggable `SignalSource` framework with registry, scoring, and pipeline orchestration
- **Signal Sources**:
  - `weather_openweather`: Weather API for hurricane/heatwave alerts (requires OPENWEATHER_API_KEY)
  - `news_search`: Google News RSS for business news, openings, expansions (no API key needed)
  - `reddit_local`: Reddit posts from South Florida subreddits (may be rate-limited)
  - `synthetic_demo`: Demo signals for testing/development
- **Signal Scoring (0-100)**: Weighted composite of:
  - Category urgency (30%): HURRICANE=95, GROWTH_SIGNAL=80, REVIEW=70, etc.
  - Recency decay (25%): Newer signals score higher
  - Geography match (25%): +25 for Miami/Broward/South Florida
  - Niche match (20%): +20 for configured niche (HVAC, roofing, etc.)
- **LeadEvent Generation**: Signals scoring >= 65 automatically create LeadEvents
- **SIGNAL_MODE** environment variable:
  - `PRODUCTION`: Full pipeline, creates LeadEvents
  - `SANDBOX`: Runs sources but skips LeadEvent creation (default)
  - `OFF`: Disables signal ingestion entirely
- **Admin Panel**: SignalNet Intelligence panel in admin console shows:
  - Mode status, last run, total signals
  - Per-source status (enabled, last run, errors, auto-disable state)
  - Recent signal stream with scores and action buttons
  - Controls: Run Now, Change Mode, Clear Old Signals
  - Signal Actions: Promote to Lead, Discard, Flag as Noisy
- **DRY_RUN Mode**: `SIGNAL_DRY_RUN=True` enables testing without external API calls
- **Structured Logging**: `SignalLog` model tracks all signal activity for debugging
- **Per-Source Throttling**: Auto-disables sources with 5+ consecutive errors, with reset capability

- **Signals Engine ("Ethical Briefcase System"):** Transforms generic lead generation into context-aware intelligence. Categorizes `LeadEvents` (e.g., HURRICANE_SEASON, COMPETITOR_SHIFT) with urgency scoring (0-100) and Miami-tuned heuristics.
- **Reports System:** Auto-generated from completed tasks, viewable in the customer portal.
- **Data Models:** Comprehensive models for `SystemSettings`, `Lead`, `Customer`, `Task`, `Invoice`, `TrialIdentity`, `Signal`, `LeadEvent`, `BusinessProfile`, `Report`, `PendingOutbound`, `PasswordResetToken`.
- **Email Infrastructure:** Supports SendGrid/SMTP with CC/Reply-To, do-not-contact enforcement, and a DRY_RUN mode.
- **Lead Generation:** Configurable sourcing with domain-based deduplication.
- **Stripe Integration:** Manages subscription checkout, payment links, billing portal, and webhook processing.
- **Autopilot:** Automates lead generation, BizDev, Onboarding, Ops, and Billing cycles every 15 minutes (paid plans only). Customers can enable/disable autopilot from their portal settings (`customer.autopilot_enabled`), and admins can control the global autopilot via `/admin/autopilot` endpoint.

**Subscription Model:**
- **Trial Plan (7 days):** Soft caps (15 tasks, 20 leads), DRY_RUN email mode only, no billing/autopilot.
- **Paid Plan ($99/month):** Full access, unlimited tasks/leads, real email sending, full billing/autopilot.
- **Customer Flows:** Defined flows for signup (with abuse prevention), upgrade (via Stripe Checkout), and cancellation (with period-end access).

## Production Configuration

**RELEASE_MODE System:** Controls production behavior.
- `RELEASE_MODE=PRODUCTION`: Production mode (required for real leads)

**EMAIL_MODE System:** Controls email sending behavior.
- `EMAIL_MODE=SMTP`: Requires SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL
- `EMAIL_MODE=SENDGRID`: Requires SENDGRID_API_KEY
- `EMAIL_MODE=DRY_RUN`: Logs emails without sending (testing only)

**Lead Source System:** SignalNet is the PRIMARY lead generator. Apollo is metadata-only.
- SignalNet monitors real-world signals (news, weather, social) and auto-creates LeadEvents for scores >= 60
- Apollo is used ONLY for company metadata enrichment (free tier) - People Search API disabled
- Lead enrichment pipeline uses free-tier services: Hunter.io, Clearbit, web scraping
- Autopilot pipeline: `SignalNet → Score → LeadEvents → Enrich → BizDev → Email`

**Required Secrets for Production:**
- `APOLLO_API_KEY`: Apollo.io API key (metadata only - get from apollo.io/settings/api-keys)
- `STRIPE_API_KEY`: Stripe secret API key for payment processing
- `STRIPE_WEBHOOK_SECRET`: Webhook signing secret
- `SMTP_HOST`: SMTP server hostname (e.g., smtp.gmail.com)
- `SMTP_USERNAME`: SMTP username/email
- `SMTP_PASSWORD`: SMTP password or app password
- `SMTP_FROM_EMAIL`: From email address
- `ADMIN_PASSWORD`: Admin console password

**Optional Secrets for SignalNet:**
- `OPENWEATHER_API_KEY`: OpenWeatherMap API key for weather alerts (free tier available)
- `SIGNAL_MODE`: Set to `PRODUCTION` to enable LeadEvent creation from signals (default: PRODUCTION)

**Optional Secrets for Lead Enrichment (free tiers available):**
- `HUNTER_API_KEY`: Hunter.io API key for email discovery (25 free requests/month)
- `CLEARBIT_API_KEY`: Clearbit API key for company enrichment (free tier available)

**Production Cleanup:** Admin console has "PURGE TEST DATA" button to remove old test data.

## External Dependencies
- **FastAPI**: Primary web framework for the backend.
- **SQLModel**: ORM for data modeling and interaction.
- **PostgreSQL**: Production database (Neon-backed via Replit).
- **bcrypt**: Used for secure password hashing.
- **SendGrid / SMTP**: Email service providers for sending outreach and system emails.
- **Stripe**: Payment gateway for managing subscriptions, invoices, and billing.
- **Apollo.io**: Lead generation API for finding real business contacts in Miami/South Florida.