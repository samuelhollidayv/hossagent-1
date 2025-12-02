# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system featuring a noir aesthetic. It orchestrates four AI agents (BizDev, Onboarding, Ops, Billing) to autonomously find leads, convert them into customers, execute tasks, and generate Stripe-powered invoices, all while tracking real-time profit. The system provides customer-facing and admin interfaces, including a customer portal for invoice management.

**Business Model:** $99/month SaaS subscription with 7-day free trial. Trial users have restricted access (15 tasks, 20 leads max) and limited features.

## User Preferences
Not specified.

## System Architecture
HossAgent is built on a FastAPI backend, utilizing `SQLModel` for data persistence with SQLite, ensuring schema auto-creation and migration. The system's core functionality revolves around asynchronous agent cycles that operate idempotently.

**UI/UX Design:**
The system adopts a "black-label noir" aesthetic with deep black backgrounds (`#0a0a0a`), Georgia serif for the customer UI, Courier monospace for the admin console, and stark white/green/red accents. Gradients and emojis are explicitly avoided.
- **Customer Dashboard (`/`)**: Displays metrics, recent work, invoices, and lead pipeline.
- **Admin Console (`/admin`)**: Provides control over agents, system status, subscription management, and data tables.
- **Customer Portal (`/portal/<token>`)**: A client-facing interface for viewing and paying invoices via Stripe, with plan status display.

**Core Features:**
- **Autonomous Agents**:
    - **BizDev Cycle**: Prospects new leads via outreach emails.
    - **Onboarding Cycle**: Converts qualified leads into customers and initiates tasks.
    - **Ops Cycle**: Executes tasks, calculating reward, cost, and profit.
    - **Billing Cycle**: Generates invoices and integrates with Stripe for payment links.
- **Data Models**: `SystemSettings`, `Lead`, `Customer`, `Task`, `Invoice`, `TrialIdentity` manage system state and business entities.
- **Email Infrastructure**: Supports SendGrid and SMTP with robust throttling and DRY_RUN mode for safety.
- **Lead Generation**: Configurable lead sourcing with domain-based deduplication.
- **Stripe Integration**: Handles invoice payment link generation, subscription billing, webhook processing, and payment status updates.

**Autopilot Flow:**
When enabled, the autopilot runs every 5 minutes, executing lead generation, BizDev, Onboarding, Ops, and Billing cycles sequentially. Note: Autopilot is gated to paid plans only.

## Subscription Model

**Plans:**
- **Trial Plan (7 days)**: Limited access for new customers
  - 15 tasks maximum
  - 20 leads maximum
  - DRY_RUN email mode only (no real emails sent)
  - No billing/invoicing
  - No autopilot access
  - Expired trials are locked out until upgrade

- **Paid Plan ($99/month)**: Full access
  - Unlimited tasks
  - Unlimited leads
  - Real email sending
  - Full billing/invoicing
  - Autopilot enabled
  - Stripe subscription management

**Upgrade Flow:**
1. Customer starts on trial plan automatically
2. Trial limits enforced in all agent cycles
3. Customer uses `/upgrade?customer_id=X` endpoint or Admin Console upgrade button
4. Stripe Checkout session created for $99/month subscription
5. Webhook (`/stripe/subscription-webhook`) syncs payment status
6. Customer upgraded to paid plan with full access

**Trial Abuse Prevention:**
- `TrialIdentity` table tracks email hash, IP, and user-agent fingerprints
- 90-day cooldown period between trial attempts per identity
- Prevents free trial abuse via duplicate signups

## API Endpoints

**Subscription Management:**
- `GET /api/subscription/status` - Get subscription configuration status
- `GET /api/customer/{id}/plan` - Get customer's plan status and usage
- `POST /upgrade?customer_id=X` - Upgrade customer to paid plan

**Webhooks:**
- `POST /stripe/webhook` - Handle invoice payment webhooks
- `POST /stripe/subscription-webhook` - Handle subscription lifecycle events

## Environment Variables

**Required for Stripe Subscriptions:**
- `ENABLE_STRIPE=TRUE` - Enable Stripe integration
- `STRIPE_API_KEY` - Stripe secret API key
- `STRIPE_WEBHOOK_SECRET` - Stripe webhook signing secret (recommended)

**Subscription Product (Auto-created at startup):**
The system automatically creates a Stripe product ("HossAgent Subscription") and price ($99/month) at startup if not already present. Product/price IDs are cached in environment:
- `STRIPE_SUBSCRIPTION_PRODUCT_ID` - Auto-created product ID
- `STRIPE_SUBSCRIPTION_PRICE_ID` - Auto-created price ID

**Email Configuration:**
- `DRY_RUN=TRUE` - Log emails without sending (always enabled for trial users)
- `SENDGRID_API_KEY` - SendGrid API key for email sending
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` - SMTP configuration alternative

**Lead Generation:**
- `LEAD_NICHE` - Target industry/niche for lead generation
- `LEAD_GEOGRAPHY` - Target geographic region

## File Structure

```
main.py                  # FastAPI application, routes, and agent orchestration
models.py                # SQLModel data models (Customer, Lead, Task, Invoice, TrialIdentity)
database.py              # Database connection and session management
subscription_utils.py    # Subscription logic, plan gating, trial management
stripe_utils.py          # Stripe API integration (payments, subscriptions)
agents.py                # Agent cycle implementations with plan gating
email_utils.py           # Email sending infrastructure
bizdev_templates.py      # BizDev email template management
lead_sources.py          # External lead API integration
release_mode.py          # Production mode configuration
templates/
  admin_console.html     # Admin interface with subscription panel
  customer_portal.html   # Customer-facing portal with plan status
  customer_dashboard.html # Main customer dashboard
hossagent.db            # SQLite database file
```

## External Dependencies
- **FastAPI**: Web framework for the backend.
- **SQLModel**: ORM for data modeling and interaction with SQLite.
- **SQLite**: Database for data persistence.
- **SendGrid / SMTP**: Optional email service providers for outreach.
- **Stripe**: Payment gateway for invoice processing, subscription billing, and payment links.
- **External Lead API**: Optional third-party service for lead sourcing.

## Recent Changes
- Converted from task-based billing to subscription SaaS model ($99/month)
- Implemented 7-day free trial with hard limits (15 tasks, 20 leads)
- Added `subscription_utils.py` for centralized subscription logic
- Extended Customer model with subscription fields
- Added `TrialIdentity` table for trial abuse prevention
- Updated all agent cycles to respect trial limits
- Added Stripe subscription webhooks for automatic status sync
- Added `/upgrade` endpoint for trial-to-paid conversion
- Added SUBSCRIPTION PLAN panel to Admin Console
- Updated Customer Portal with plan status display
