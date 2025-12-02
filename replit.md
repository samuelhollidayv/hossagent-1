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

**Interfaces (Separated):**
- **Admin Console (`/admin`)**: Internal operator dashboard with full control over agents, system status, subscription management, email health, lead sources, revenue/profit metrics, and data tables. Includes "View as customer" links for each customer.
- **Customer Portal (`/portal/<token>`)**: Clean, customer-facing interface showing only: Plan & Billing status, Recent Work (no cost/profit), and Invoices with Pay Now buttons. Includes "Start Paid Subscription" or "Manage Billing" CTAs.
- **Customer Dashboard (`/`)**: Public-facing metrics overview.

**Core Features:**
- **Autonomous Agents**:
    - **BizDev Cycle**: Prospects new leads via outreach emails.
    - **Onboarding Cycle**: Converts qualified leads into customers and initiates tasks.
    - **Ops Cycle**: Executes tasks, calculating reward, cost, and profit.
    - **Billing Cycle**: Generates invoices and integrates with Stripe for payment links.
- **Data Models**: `SystemSettings`, `Lead`, `Customer`, `Task`, `Invoice`, `TrialIdentity` manage system state and business entities.
- **Email Infrastructure**: Supports SendGrid and SMTP with robust throttling and DRY_RUN mode for safety.
- **Lead Generation**: Configurable lead sourcing with domain-based deduplication.
- **Stripe Integration**: Handles subscription checkout, invoice payment links, billing portal, webhook processing, and payment status updates.

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
  - Stripe subscription management via Billing Portal

**Customer Upgrade Flow:**
1. Customer visits their portal at `/portal/<public_token>`
2. Clicks "Start Paid Subscription" button
3. Redirected to `/subscribe/<public_token>` which creates Stripe Checkout session
4. Customer completes payment on Stripe's hosted checkout (supports Apple Pay, Google Pay, cards)
5. Stripe sends `checkout.session.completed` webhook to `/stripe/subscription-webhook`
6. Customer automatically upgraded to paid plan with full access
7. Customer can manage billing via "Manage Billing" button (redirects to Stripe Customer Portal)

**Trial Abuse Prevention:**
- `TrialIdentity` table tracks email hash, IP, and user-agent fingerprints
- 90-day cooldown period between trial attempts per identity
- Prevents free trial abuse via duplicate signups

## API Endpoints

**Customer Subscription:**
- `GET /portal/<public_token>` - Customer portal (view work, plan, invoices)
- `GET /subscribe/<public_token>` - Redirect to Stripe Checkout for subscription
- `GET /billing/<public_token>` - Redirect to Stripe Customer Portal (paid users)

**Admin APIs:**
- `GET /api/subscription/status` - Get subscription configuration status
- `GET /api/customer/{id}/plan` - Get customer's plan status and usage
- `POST /upgrade?customer_id=X` - Admin-triggered upgrade to paid plan

**Webhooks:**
- `POST /stripe/webhook` - Handle invoice payment webhooks
- `POST /stripe/subscription-webhook` - Handle subscription lifecycle events:
  - `checkout.session.completed` - Customer completed Stripe Checkout
  - `invoice.payment_succeeded` - Subscription payment succeeded
  - `customer.subscription.updated` - Subscription status changed
  - `customer.subscription.deleted` - Subscription canceled

## Environment Variables

**Required for Stripe Subscriptions:**
- `ENABLE_STRIPE=TRUE` - Enable Stripe integration
- `STRIPE_API_KEY` - Stripe secret API key (sk_live_... or sk_test_...)
- `STRIPE_WEBHOOK_SECRET` - Stripe webhook signing secret (whsec_..., recommended for production)
- `STRIPE_PRICE_ID_PRO` - Stripe Price ID for $99/month plan (price_..., optional if auto-created)

**Subscription Product (Auto-created at startup):**
The system automatically creates a Stripe product ("HossAgent Subscription") and price ($99/month) at startup if not already present. Alternatively, provide your own:
- `STRIPE_PRODUCT_ID` - Existing Stripe product ID
- `STRIPE_PRICE_ID` - Existing Stripe price ID

**Email Configuration:**
- `DRY_RUN=TRUE` - Log emails without sending (always enabled for trial users)
- `SENDGRID_API_KEY` - SendGrid API key for email sending
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` - SMTP configuration alternative

**Lead Generation:**
- `LEAD_NICHE` - Target industry/niche for lead generation
- `LEAD_GEOGRAPHY` - Target geographic region

## How to Get a Customer Portal URL

Each customer has a unique `public_token` stored in the database. The portal URL is:
```
/portal/<public_token>
```

To find a customer's portal URL:
1. Go to Admin Console (`/admin`)
2. Look at the CUSTOMER PLANS table
3. Click "View as customer" link to open their portal

The "Start Paid Subscription" button routes through `/subscribe/<public_token>` which:
- Creates a Stripe Checkout session if Stripe is configured
- Shows a friendly message if Stripe is disabled
- Redirects paid customers to Stripe Customer Portal instead

## File Structure

```
main.py                  # FastAPI application, routes, and agent orchestration
models.py                # SQLModel data models (Customer, Lead, Task, Invoice, TrialIdentity)
database.py              # Database connection and session management
subscription_utils.py    # Subscription logic, plan gating, checkout link creation
stripe_utils.py          # Stripe API integration (payments, subscriptions, webhooks)
agents.py                # Agent cycle implementations with plan gating
email_utils.py           # Email sending infrastructure
bizdev_templates.py      # BizDev email template management
lead_sources.py          # External lead API integration
release_mode.py          # Production mode configuration
templates/
  admin_console.html     # Internal admin interface with all metrics and controls
  customer_portal.html   # Clean customer-facing portal (Plan, Work, Invoices)
  customer_dashboard.html # Public dashboard
hossagent.db            # SQLite database file
```

## External Dependencies
- **FastAPI**: Web framework for the backend.
- **SQLModel**: ORM for data modeling and interaction with SQLite.
- **SQLite**: Database for data persistence.
- **SendGrid / SMTP**: Optional email service providers for outreach.
- **Stripe**: Payment gateway for subscription checkout, billing portal, invoice processing, and webhooks.
- **External Lead API**: Optional third-party service for lead sourcing.

## Recent Changes
- Separated customer portal from admin console (clean customer view vs internal metrics)
- Added `/subscribe/<public_token>` route for Stripe Checkout redirect
- Added `/billing/<public_token>` route for Stripe Customer Portal
- Created `get_or_create_subscription_checkout_link()` helper in subscription_utils.py
- Added `create_billing_portal_link()` helper for paid customer billing management
- Updated customer portal with three clean sections: Plan & Billing, Recent Work, Invoices
- Added "View as customer" links to Admin Console customer table
- Extended webhook handling for `checkout.session.completed` events
- Added payment success/cancelled banners to customer portal
- Updated STRIPE_PRICE_ID_PRO env var documentation
