# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system designed with a noir aesthetic. It features four autonomous agents (BizDev, Onboarding, Ops, Billing), SQLite persistence, and dual UI interfaces: a customer-facing dashboard, an admin console with autopilot control, and a customer portal for invoice viewing/payment. The system performs self-driving cycles to continuously find leads, convert them into customers, execute tasks autonomously, and generate invoices with Stripe payment links, all while tracking real-time profit.

## User Preferences
Not specified.

## System Architecture

### Core Design
HossAgent operates as a FastAPI backend, integrating routes, an autopilot mechanism, and autonomous agents. Data persistence is managed via SQLite, with schema auto-creation and auto-migration on startup. The system is structured around `SQLModel` for data models and asynchronous cycle functions for agent operations, ensuring idempotency.

### Directory Structure
- `main.py`: FastAPI application, routes, autopilot loop, webhook handlers.
- `models.py`: SQLModel data models (SystemSettings, Lead, Customer, Task, Invoice).
- `database.py`: SQLite setup, schema initialization, and auto-migrations.
- `agents.py`: Logic for the four autonomous agent cycles.
- `email_utils.py`: Email infrastructure (SendGrid, SMTP, DRY_RUN) with hourly throttling.
- `lead_sources.py`: Lead source providers (DummySeed for dev, SearchApi for production).
- `lead_service.py`: Lead generation service with domain-based deduplication and logging.
- `release_mode.py`: Release mode configuration and startup banners.
- `bizdev_templates.py`: Niche-tuned email template engine with multiple packs.
- `stripe_utils.py`: Stripe payment link creation and webhook handling.
- `templates/`: HTML templates (dashboard, admin_console, customer_portal).

### Data Models
- **SystemSettings**: Global system flags like `autopilot_enabled`.
- **Lead**: Prospecting records with statuses (new, contacted, responded, qualified, email_failed, dead). Includes `website` and `source` fields.
- **Customer**: Converted leads with billing info, `stripe_customer_id`, and `public_token` for portal access.
- **Task**: Units of work tracking reward, cost, and profit.
- **Invoice**: Billing records with `payment_url` for Stripe payment links and `stripe_payment_id`.

### Autonomous Agents
1. **BizDev Cycle**: Sends outreach emails to NEW leads using template engine.
2. **Onboarding Cycle**: Converts contacted/responded leads to customers with initial tasks.
3. **Ops Cycle**: Executes pending tasks and calculates profit.
4. **Billing Cycle**: Generates invoices and creates Stripe payment links.

### UI/UX Design
The system employs a "black-label noir" aesthetic: deep black background (`#0a0a0a`), Georgia serif for customer UI, Courier monospace for admin console, stark white/green/red accents. No gradients or emojis.

- **Customer Dashboard (`/`)**: Read-only metrics, recent work, invoices, lead pipeline.
- **Admin Console (`/admin`)**: Control room with agent buttons, system status panels, data tables.
- **Customer Portal (`/portal/<token>`)**: Client-facing invoice view with Stripe payment buttons.

## Environment Variables

### Email Configuration
```
EMAIL_MODE=DRY_RUN|SENDGRID|SMTP  # Default: DRY_RUN

# For SENDGRID mode:
SENDGRID_API_KEY=sg_...
SENDGRID_FROM_EMAIL=you@domain.com

# For SMTP mode:
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your_user
SMTP_PASSWORD=your_pass
SMTP_FROM_EMAIL=you@domain.com

# Throttling:
MAX_EMAILS_PER_CYCLE=10    # Default: 10
MAX_EMAILS_PER_HOUR=50     # Default: 50
```

### Lead Generation
```
LEAD_NICHE=small B2B marketing agencies that sell retainers
LEAD_GEOGRAPHY=US & Canada
LEAD_MIN_COMPANY_SIZE=5
LEAD_MAX_COMPANY_SIZE=50
MAX_NEW_LEADS_PER_CYCLE=10

# For real lead API:
LEAD_SEARCH_API_URL=https://api.leadprovider.com/search
LEAD_SEARCH_API_KEY=your_api_key
```

### BizDev Templates
```
BIZDEV_NICHE_TEMPLATE=general  # Options: general, agency, saas, consulting, revops
BIZDEV_SENDER_NAME=HossAgent
BIZDEV_SENDER_EMAIL=hello@yourdomain.com
BIZDEV_OFFER=autonomous business operations
```

### Stripe Billing
```
ENABLE_STRIPE=TRUE|FALSE           # Default: FALSE
STRIPE_API_KEY=sk_...              # Secret key (starts with sk_)
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_DEFAULT_CURRENCY=usd        # Default: usd
STRIPE_MIN_AMOUNT_CENTS=100        # Minimum invoice amount (default: $1.00)
STRIPE_MAX_AMOUNT_CENTS=50000      # Maximum invoice amount (default: $500.00)
```

### Release Mode
```
RELEASE_MODE=PRODUCTION|STAGING|DEVELOPMENT  # Default: DEVELOPMENT
```

**PRODUCTION mode:**
- Strict validation of all credentials at startup
- Warnings for high-volume email settings (>100/hour)
- Enforces DRY_RUN fallback for missing credentials
- Production safety banners printed to console

**STAGING mode:**
- Semi-strict validation (warnings but continues)
- Lower recommended throttle limits for testing
- Good for pre-production testing

**DEVELOPMENT mode (default):**
- Lenient configuration (DRY_RUN acceptable)
- No high-volume warnings
- Allows DummySeed providers without warnings

## API Endpoints

### Pages
- `GET /` - Customer Dashboard
- `GET /admin` - Admin Console
- `GET /portal/<public_token>` - Customer Portal

### Data APIs
- `GET /api/leads` - All leads
- `GET /api/customers` - All customers
- `GET /api/tasks` - All tasks
- `GET /api/invoices` - All invoices
- `GET /api/settings` - System settings + email status
- `GET /api/email-log` - Recent email attempts
- `GET /api/lead-source` - Lead source status
- `GET /api/stripe/status` - Stripe configuration status
- `GET /api/bizdev/templates` - Template engine status
- `GET /api/release-mode` - Release mode configuration status
- `GET /admin/summary?hours=24` - Aggregated activity summary (1-168 hours)

### Admin Actions
- `POST /admin/autopilot?enabled=true|false` - Toggle autopilot
- `POST /admin/send-test-email?to_email=...` - Test email config
- `POST /api/run/lead-source` - Run lead generation
- `POST /api/run/bizdev` - Run BizDev cycle
- `POST /api/run/onboarding` - Run Onboarding cycle
- `POST /api/run/ops` - Run Ops cycle
- `POST /api/run/billing` - Run Billing cycle
- `POST /api/invoices/<id>/mark-paid` - Mark invoice paid (testing)

### Webhooks
- `POST /stripe/webhook` - Stripe payment webhook (configure in Stripe dashboard)

## Safety Rules

### DRY_RUN Fallback
The system NEVER sends real emails or creates real charges if configuration is missing:
- Missing SENDGRID/SMTP credentials -> DRY_RUN mode
- Missing STRIPE_API_KEY -> No payment links created
- All failures logged with `[DRY_RUN_FALLBACK]` prefix

### Throttling
- Per-cycle limit: MAX_EMAILS_PER_CYCLE (default 10)
- Per-hour limit: MAX_EMAILS_PER_HOUR (default 50)
- Limits enforced regardless of mode

### Invoice Safety
- Stripe payment links only created for amounts $1-$500 (configurable)
- Outside bounds: logged and skipped, no crash

## Customer Payments & Portal

### Environment Variables
```
ENABLE_STRIPE=TRUE|FALSE           # Default: FALSE
STRIPE_API_KEY=sk_...              # Stripe secret key
STRIPE_WEBHOOK_SECRET=whsec_...    # Optional but recommended
STRIPE_DEFAULT_CURRENCY=usd        # Default: usd
STRIPE_MIN_AMOUNT_CENTS=100        # Default: $1.00
STRIPE_MAX_AMOUNT_CENTS=50000      # Default: $500.00
```

### Behavior

**When Stripe is disabled (ENABLE_STRIPE=FALSE):**
- No PAY NOW buttons in Customer Portal
- Portal shows: "Online payments are currently disabled. Contact your operator for payment options."
- Admin Console shows Stripe status as DISABLED

**When Stripe is enabled and configured:**
- New invoices get Stripe payment links automatically during Billing cycle
- Old unpaid invoices get payment links via retroactive pass on startup
- Customer Portal shows PAY NOW buttons for invoices with payment links
- Admin Console shows Stripe status, payment link counts, and missing link warnings

**When Stripe is enabled but misconfigured:**
- No PAY NOW buttons (safety fallback)
- Portal shows: "Online payments temporarily unavailable."
- Logged as [STRIPE][DRY_RUN_FALLBACK]

### Customer Portal Features
- Account Summary: Total Invoiced, Total Paid, Outstanding balance
- Outstanding Invoices table with PAY NOW buttons (when available)
- Payment History table with PAID badges
- Recent Work table showing task status

### Admin Console Features
- Stripe Billing panel with status, currency, limits
- Payment link counts (with links vs missing links)
- Invoices table with LINK OK / MISSING LINK badges
- Webhook configuration status

### Testing Stripe Payments
1. Set `ENABLE_STRIPE=TRUE` and `STRIPE_API_KEY` to a valid test key
2. Create or trigger a new invoice via Billing agent
3. Verify:
   - Admin Console shows payment_url for that invoice
   - Customer Portal shows PAY NOW button
4. Use Stripe test mode to simulate payment
5. Confirm invoice status updates to PAID and button disappears

### Error Handling
- All agent cycles catch and log exceptions
- Autopilot loop never crashes
- Webhook signature validation on all Stripe events

## Testing

### Test Email Configuration
```bash
curl -X POST "http://localhost:5000/admin/send-test-email?to_email=test@example.com"
```

### Test Stripe Webhook (local)
Use Stripe CLI:
```bash
stripe listen --forward-to localhost:5000/stripe/webhook
```

### Customer Portal Access
Each customer has a `public_token`. Access portal at:
```
/portal/<public_token>
```

## Autopilot Flow
When enabled, runs every 5 minutes:
1. **Lead Generation** - Fetch candidates, deduplicate, create leads
2. **BizDev** - Send personalized emails to NEW leads
3. **Onboarding** - Convert contacted leads to customers
4. **Ops** - Execute pending tasks
5. **Billing** - Generate invoices + payment links

## External Dependencies
- **FastAPI**: Web framework
- **SQLModel**: ORM with SQLite
- **SendGrid (Optional)**: Email delivery
- **SMTP (Optional)**: Email delivery
- **Stripe (Optional)**: Payment processing
- **External Lead API (Optional)**: Real lead sourcing
