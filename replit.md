# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system featuring a noir aesthetic. It orchestrates four AI agents (BizDev, Onboarding, Ops, Billing) to autonomously find leads, convert them into customers, execute tasks, and generate Stripe-powered invoices, all while tracking real-time profit. The system provides customer-facing and admin interfaces with complete authentication.

**Business Model:** $99/month SaaS subscription with 7-day free trial. Trial users have restricted access (15 tasks, 20 leads max) and limited features.

## User Preferences
Not specified.

## System Architecture
HossAgent is built on a FastAPI backend, utilizing `SQLModel` for data persistence with SQLite, ensuring schema auto-creation and migration. The system's core functionality revolves around asynchronous agent cycles that operate idempotently.

**UI/UX Design:**
The system adopts a "black-label noir" aesthetic with deep black backgrounds (`#0a0a0a`), Georgia serif for the customer UI, Courier monospace for the admin console, and stark white/green/red accents. Gradients and emojis are explicitly avoided.

**Interfaces (Separated):**
- **Marketing Landing Page (`/`)**: Public-facing homepage with "Start 7-Day Free Trial" and "Book a Demo" CTAs.
- **Admin Console (`/admin`)**: Internal operator dashboard with password protection, full control over agents, system status, subscription management, email health, lead sources, revenue/profit metrics, and data tables. Includes "View as customer" links for each customer.
- **Customer Portal (`/portal`)**: Session-authenticated portal showing: Plan & Billing status, Recent Work, and Invoices with Pay Now buttons. Includes "Start Paid Subscription" or "Manage Billing" CTAs.
- **Customer Portal - Admin View (`/portal/<token>`)**: Token-based access for admin impersonation of customer accounts.

## Authentication System

**Customer Authentication:**
- Email + password authentication with bcrypt hashing
- Session-based with secure HTTP-only cookies (`hossagent_session`)
- Sessions last 30 days
- Routes:
  - `GET /signup` - Trial registration form
  - `POST /signup` - Process registration with trial abuse prevention
  - `GET /login` - Customer login form
  - `POST /login` - Process login and set session
  - `GET /logout` - Clear session and redirect to home
  - `GET /portal` - Session-authenticated customer portal

**Admin Authentication:**
- Simple password gate stored in `ADMIN_PASSWORD` environment variable
- Separate session cookie (`hossagent_admin`)
- Routes:
  - `GET /admin` - Redirects to login if not authenticated
  - `GET /admin/login` - Admin password form
  - `POST /admin/login` - Process admin login

**Trial Abuse Prevention:**
- `TrialIdentity` table tracks email hash, IP, and user-agent fingerprints
- 90-day cooldown period between trial attempts per identity
- Password requirements: minimum 8 characters

**Core Features:**
- **Autonomous Agents**:
    - **Signals Agent**: Monitors external context signals (job postings, reviews, competitor updates, permits, weather) and generates actionable LeadEvents for moment-aware outreach.
    - **BizDev Cycle**: Prospects new leads via outreach emails (standard templates).
    - **Event-Driven BizDev Cycle**: Sends contextual outreach emails based on LeadEvents with Miami-tuned templates.
    - **Onboarding Cycle**: Converts qualified leads into customers and initiates tasks.
    - **Ops Cycle**: Executes tasks, calculating reward, cost, and profit.
    - **Billing Cycle**: Generates invoices and integrates with Stripe for payment links.
- **Signals Engine**: The "Ethical Briefcase System" - transforms HossAgent from generic lead gen into a context-aware intelligence engine:
    - **Signal Types**: job_posting, review, competitor_update, permit, weather, news, demographics
    - **LeadEvent Categories**: HURRICANE_SEASON, COMPETITOR_SHIFT, GROWTH_SIGNAL, BILINGUAL_OPPORTUNITY, REPUTATION_CHANGE, MIAMI_PRICE_MOVE, OPPORTUNITY
    - **Urgency Scoring**: 0-100 scale, events with urgency >= 75 are marked as "[Time-Sensitive]"
    - **Miami-Tuned Heuristics**: Templates and categories tailored to South Florida market
- **Data Models**: `SystemSettings`, `Lead`, `Customer`, `Task`, `Invoice`, `TrialIdentity`, `Signal`, `LeadEvent` manage system state and business entities.
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

**Customer Signup Flow:**
1. User visits marketing landing page at `/`
2. Clicks "Start 7-Day Free Trial" button
3. Completes signup form at `/signup` with company name, email, password
4. Trial abuse prevention validates fingerprint (email, IP, user-agent)
5. Customer account created with trial status, redirected to portal

**Customer Upgrade Flow:**
1. Customer logs in and visits portal at `/portal`
2. Clicks "Start Paid Subscription" button
3. Redirected to Stripe Checkout via `/subscribe/<public_token>`
4. Customer completes payment on Stripe's hosted checkout (supports Apple Pay, Google Pay, cards)
5. Stripe sends `checkout.session.completed` webhook to `/stripe/subscription-webhook`
6. Customer automatically upgraded to paid plan with full access
7. Customer can manage billing via "Manage Billing" button (redirects to Stripe Customer Portal)

## API Endpoints

**Public Routes:**
- `GET /` - Marketing landing page
- `GET /signup` - Customer signup form
- `POST /signup` - Process customer registration
- `GET /login` - Customer login form
- `POST /login` - Process customer login
- `GET /logout` - Logout and clear session

**Customer Portal (Session Required):**
- `GET /portal` - Authenticated customer portal
- `GET /subscribe/<public_token>` - Redirect to Stripe Checkout
- `GET /billing/<public_token>` - Redirect to Stripe Billing Portal

**Admin Routes (Admin Session Required):**
- `GET /admin` - Admin console (redirects to login if not authenticated)
- `GET /admin/login` - Admin login form
- `POST /admin/login` - Process admin login
- `GET /portal/<public_token>` - View customer portal as admin

**Admin APIs:**
- `GET /api/subscription/status` - Get subscription configuration status
- `GET /api/customer/{id}/plan` - Get customer's plan status and usage
- `POST /upgrade?customer_id=X` - Admin-triggered upgrade to paid plan

**Webhooks:**
- `POST /stripe/webhook` - Handle invoice payment webhooks
- `POST /stripe/subscription-webhook` - Handle subscription lifecycle events

## Environment Variables

**Required for Admin Authentication:**
- `ADMIN_PASSWORD` - Password for admin console access (set in Secrets)

**Required for Stripe Subscriptions:**
- `ENABLE_STRIPE=TRUE` - Enable Stripe integration
- `STRIPE_API_KEY` - Stripe secret API key (sk_live_... or sk_test_...)
- `STRIPE_WEBHOOK_SECRET` - Stripe webhook signing secret (whsec_...)
- `STRIPE_PRICE_ID_PRO` - Stripe Price ID for $99/month plan (price_..., optional)

**Subscription Product (Auto-created at startup):**
The system automatically creates a Stripe product ("HossAgent Subscription") and price ($99/month) at startup if not already present.

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
auth_utils.py            # Authentication utilities (password hashing, sessions)
models.py                # SQLModel data models (Customer, Lead, Task, Invoice, TrialIdentity, Signal, LeadEvent)
database.py              # Database connection and session management
subscription_utils.py    # Subscription logic, plan gating, checkout link creation
stripe_utils.py          # Stripe API integration (payments, subscriptions, webhooks)
agents.py                # Agent cycle implementations with plan gating (includes event-driven BizDev)
signals_agent.py         # Signals Engine: context monitoring and LeadEvent generation
email_utils.py           # Email sending infrastructure
bizdev_templates.py      # BizDev email template management
lead_sources.py          # External lead API integration
release_mode.py          # Production mode configuration
templates/
  marketing_landing.html # Public homepage with trial CTA
  auth_signup.html       # Customer signup form
  auth_login.html        # Customer login form
  admin_login.html       # Admin password form
  admin_console.html     # Internal admin interface with Signals Engine controls
  customer_portal.html   # Clean customer-facing portal with Today's Opportunities
hossagent.db            # SQLite database file
```

## External Dependencies
- **FastAPI**: Web framework for the backend.
- **SQLModel**: ORM for data modeling and interaction with SQLite.
- **SQLite**: Database for data persistence.
- **bcrypt**: Password hashing for customer authentication.
- **SendGrid / SMTP**: Optional email service providers for outreach.
- **Stripe**: Payment gateway for subscription checkout, billing portal, invoice processing, and webhooks.
- **External Lead API**: Optional third-party service for lead sourcing.

## Recent Changes
- **Signals Engine Implementation**: Added complete context-aware intelligence system
  - Created Signal and LeadEvent database models for tracking opportunities
  - Created signals_agent.py with Miami-tuned heuristics for 7 signal types
  - Added event-driven BizDev cycle with contextual email templates
  - Integrated "Today's Opportunities" panel in customer portal (urgency-sorted)
  - Added Signals Engine section in admin console with run controls
  - Urgency scoring with fire icons for high-priority events (70+)
  - Time-sensitive flagging for events with urgency >= 75
- Added complete authentication system with email+password login
- Created marketing landing page at / with trial CTA
- Implemented customer signup flow with trial abuse prevention
- Added session-based /portal access (requires login)
- Added /portal/<token> for admin impersonation
- Created admin authentication protecting /admin console
- Added auth_utils.py for password hashing and session management
- Customer model extended with password_hash, contact_name, niche, geography fields
- Customer portal hides micro-invoices (under $10) for cleaner UX
- Added logout button to customer portal header
