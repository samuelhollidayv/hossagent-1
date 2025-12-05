# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system with a noir aesthetic. Its core purpose is to autonomously identify leads, convert them into customers, execute tasks, and manage invoicing and profit tracking via Stripe. The system orchestrates four specialized AI agents (BizDev, Onboarding, Ops, Billing) and offers both customer-facing and administrative interfaces with robust authentication. The business model is a $99/month SaaS subscription, including a 7-day free trial. The project aims to provide a comprehensive, autonomous business solution, initially targeting the South Florida market with ambitions for broader application.

## User Preferences
- Pure web scraping only, NO paid APIs (Apollo.io explicitly forbidden)
- Target 5-10% enrichment success rate improvement from ~2% baseline

## System Architecture
HossAgent utilizes a FastAPI backend and SQLModel for ORM, connecting to PostgreSQL in production and SQLite for development. The system emphasizes asynchronous, idempotent agent cycles.

**UI/UX Design (2025 Professional Noir):**
- **Fonts**: Inter (customer-facing) and JetBrains Mono (admin).
- **Color System**: Deep black (`#0a0a0a`), elevated surfaces (`#111111`), subtle borders (`#1f1f1f`), white text (`#ffffff`), and accent green (`#22c55e`).
- **Design Patterns**: Modern rounded corners, smooth transitions, hover effects, and backdrop blur. No gradients or emojis.

**Interfaces:**
- **Public**: Marketing Landing Page (`/`), About Page (`/about`), How It Works Page (`/how-it-works`).
- **Admin Console (`/admin`)**: Consolidated dashboard for Signals, LeadEvents, Outbound Messages, and Customers.
- **Customer Portal (`/portal`)**: Session-authenticated portal with sections for Account Status, Recent Opportunities & Outreach, Conversations (email threads), and Reports. Includes a token-based admin impersonation view (`/portal/<token>`).
- **Customer Settings (`/portal/settings`)**: Business profile configuration, outreach preferences, and do-not-contact lists.

**Authentication System:**
- **Customer**: Email + bcrypt password, 14-day session-based HTTP-only cookies, password reset.
- **Admin**: Password gate via `ADMIN_PASSWORD` environment variable.
- **Trial Abuse Prevention**: Tracks email hash, IP, user-agent fingerprints, with 90-day cooldown.

**Core Features & Technical Implementations:**
- **Autonomous Agents**: SignalNet, BizDev, Onboarding, Ops, Billing cycles.
- **SignalNet System**: 24/7 autonomous signal ingestion with pluggable `SignalSource` framework, scoring, and `LeadEvent` generation for signals scoring >= 65.
- **Contextual Opportunity Engine**: Manages `AUTO` (immediate send) and `REVIEW` (customer approval) outreach modes, leveraging `BusinessProfile` for customer preferences and enforcing do-not-contact lists.
- **Outbound Email System**: Uses `hossagent.net` (SendGrid authenticated), prioritizes person-like emails, rotates subject lines, supports `transparent_ai` or `classic` templates, and tailors messages based on signal type (`market_entry`, `competitor_intel`, `growth_opportunity`, `market_shift`). Includes 3 actionable recommendations per email, name parsing, rate limiting, and suppression flow.
- **HossNative Lead Discovery**: Autonomous lead generation system integrating with SignalNet, performing web scraping for emails, domain resolution, and email validation without external APIs.

**OPERATION ARCHANGEL v2 (Multi-Layered Enrichment Engine):**
- **State Machine**: Tracks enrichment lifecycle (UNENRICHED → ENRICHING → ENRICHED/OUTBOUND_READY/ARCHIVED_UNENRICHABLE)
- **Budget System**: max_enrichment_attempts (default 3) per lead before marking ARCHIVED_UNENRICHABLE
- **Mission Log**: JSON array tracking [method, success, timestamp, details] per enrichment attempt
- **Company Table**: Canonical entity storage with domain+name upserts, supports lead attachment for reuse
- **NameStorm**: Multi-candidate company name extraction with branded validation (rejects generics like "Texas HVAC company", accepts "Miami Best Roofing")
- **DomainStorm**: Multi-layered domain discovery (Google CSE, Bing, DuckDuckGo fallback)
- **EmailStorm**: Layered email discovery with confidence scoring (person-like vs generic)
- **PhoneStorm**: Extracts, normalizes, validates phone numbers from web pages
- **EnrichmentMetrics**: Per-source yield tracking for optimization

**Admin API Endpoints (ARCHANGEL v2):**
- `GET /api/lead_events`: Includes enrichment_status, enrichment_attempts, unenrichable_reason, confidence scores
- `GET /api/enrichment/metrics`: Source-level enrichment yield, discovery counts, unenrichable breakdown
- `GET /api/companies`: Canonical company entities with enrichment status

**Craigslist Connector (EPIC 3.1):**
- SMB-heavy signal source with niche detection (HVAC, plumbing, roofing, legal, etc.)
- Job posting and service listing extraction for lead generation

**Job Board Connector (EPIC 3.2):**
- Indeed, ZipRecruiter, Glassdoor integration for SMB hiring signals
- Detects HVAC, plumber, roofing hiring patterns in South Florida
- 1-hour caching layer (CACHE_TTL = 3600) with backoff protection
- Registered as `JobBoardSignalSource` in SignalNet pipeline

**MacroStorm Strategic Intelligence (EPIC 3.3):**
- SEC EDGAR Connector: Ingests 10-K, 10-Q, 8-K filings from SEC RSS feeds
- NLP extraction for expansions, contractions, closures, and M&A events
- ForceCast Mapping Engine: Maps MacroEvents to SMB target profiles
- 4-hour cooldown in autopilot loop (`edgar_cooldown = 14400`)
- Creates LeadEvents with macro_event_id linkage

**Caching Layers:**
- Job Board Connector: 1-hour cache for HTTP requests
- Lead Enrichment: 1-hour cache for article body fetches
- DuckDuckGo: Exponential backoff (5 min → 10 min → 20 min)

- **Autopilot**: Automates agent cycles every 15 minutes for paid plans.
- **Conversation Engine**: Handles inbound email replies, AI-assisted draft generation, guardrails for sensitive content, human-in-the-loop approval, and a suppression system. Uses a state machine (OPEN → HUMAN_OWNED → AUTO → CLOSED).
- **Subscription Model**: Trial plan (7 days, restricted) and Paid plan ($99/month, full access). Manages signup, Stripe checkout, upgrade, and cancellation flows.
- **Analytics & Telemetry**: Server-side analytics (`analytics.py`) tracks page views, funnel events, and abandonment (stored in `analytics_events.json` with IP hashing). An Admin Analytics Dashboard provides insights. Optional Google Analytics 4 integration.

## Key Files (ARCHANGEL v2)
- `models.py`: LeadEvent (enrichment fields), Company, EnrichmentMetrics tables
- `mission_log.py`: Mission log tracking system
- `lead_enrichment.py`: Main enrichment pipeline with state machine
- `email_storm.py`: EmailStorm layered email discovery
- `craigslist_connector.py`: Craigslist SMB signal source
- `job_board_connector.py`: Indeed/ZipRecruiter/Glassdoor job board connector
- `forcecast_engine.py`: MacroEvent to SMB target mapping
- `sec_edgar_connector.py`: SEC EDGAR filings ingestion
- `company_name_extraction.py`: NameStorm branded extraction
- `domain_discovery.py`: DomainStorm multi-layer discovery
- `phone_extraction.py`: PhoneStorm extraction and validation

## External Dependencies
- **FastAPI**: Backend web framework.
- **SQLModel**: ORM.
- **PostgreSQL**: Production database.
- **bcrypt**: Password hashing.
- **SendGrid**: Primary email service provider.
- **Amazon SES**: Alternative email service provider.
- **Stripe**: Payment gateway for subscriptions.
- **OpenWeatherMap**: Used by SignalNet for weather data.
