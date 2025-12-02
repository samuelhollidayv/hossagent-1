# HossAgent - Autonomous AI Business Engine

## Overview
A complete noir-aesthetic autonomous business system with four autonomous agents (BizDev, Onboarding, Ops, Billing), SQLite persistence, and dual UI interfaces: a customer-facing dashboard and an admin console with autopilot control.

The system runs **self-driving cycles** that continuously find leads, convert them to customers, execute tasks autonomously, and generate invoices—all with real-time profit tracking.

## Architecture

### Directory Structure
```
hoss-agent/
├── main.py              # FastAPI backend with routes and autopilot
├── models.py            # SQLModel data models + SystemSettings
├── database.py          # SQLite setup with schema init
├── agents.py            # Four autonomous agent cycle functions
├── templates/
│   ├── dashboard.html   # Customer-facing read-only dashboard (/)
│   └── admin_console.html # Operator control room (/admin)
├── requirements.txt     # Python dependencies
└── hossagent.db        # SQLite database (auto-created)
```

### Data Models
- **SystemSettings**: Global flags (autopilot_enabled)
- **Lead**: Company prospecting records (new → contacted → responded → qualified → dead)
- **Customer**: Converted leads with billing info + stripe_customer_id field
- **Task**: Work units with reward, cost, and profit tracking
- **Invoice**: Billing records aggregating task profits

### Autonomous Agents (Async Cycle Functions)
All agents run as idempotent `*_cycle` functions that can be called repeatedly:

1. **BizDev Cycle** - Generates 1-2 realistic leads with corporate names
2. **Onboarding Cycle** - Converts new/responded leads to customers, creates template tasks
3. **Ops Cycle** - Picks pending tasks, executes work (stub for OpenAI integration), calculates profit
4. **Billing Cycle** - Aggregates completed tasks per customer, generates draft invoices

## Routes & Interfaces

### Customer-Facing (Public Read-Only)
- **GET /** → Customer Dashboard
  - Summary metrics: Total revenue, outstanding invoices, completed tasks, active leads
  - Recent work completed table
  - Invoices & billing table
  - Leads pipeline table
  - Auto-refreshes every 30 seconds

### Operator Controls (Admin Console)
- **GET /admin** → Admin Console
  - Autopilot toggle (ON/OFF button)
  - Manual agent trigger buttons: RUN BIZDEV, RUN ONBOARDING, RUN OPS, RUN BILLING
  - Live execution log with timestamps
  - Data tables: Recent tasks, draft invoices, recent leads
  - All metrics update in real-time

### Data APIs (JSON)
- GET /api/leads - List all leads
- GET /api/customers - List all customers
- GET /api/tasks - List all tasks with cost/profit
- GET /api/invoices - List all invoices
- GET /api/settings - Get autopilot status

### Admin APIs
- POST /admin/autopilot?enabled=true/false - Toggle autopilot mode
- POST /api/run/bizdev - Manually trigger BizDev cycle
- POST /api/run/onboarding - Manually trigger Onboarding cycle
- POST /api/run/ops - Manually trigger Ops cycle
- POST /api/run/billing - Manually trigger Billing cycle
- POST /api/invoices/{id}/mark-paid - Mark invoice as paid

### Detail Pages
- GET /customers/{id} - Customer detail with tasks & invoices
- GET /leads/{id} - Lead detail with customer context & tasks
- GET /invoices/{id} - Invoice detail with related tasks

## Background Autopilot Loop

On app startup, a background task runs:

```python
async def autopilot_loop():
    while True:
        with Session(engine) as session:
            settings = session.exec(select(SystemSettings)).first()
            if settings.autopilot_enabled:
                # Run all cycles in sequence
                await run_bizdev_cycle(session)
                await run_onboarding_cycle(session)
                await run_ops_cycle(session)
                await run_billing_cycle(session)
        await asyncio.sleep(300)  # 5 minutes between cycles
```

**Key Features:**
- Runs every 5 minutes when enabled
- Gracefully handles errors (logs, continues running)
- Can be toggled ON/OFF via admin console or API
- Fully idempotent - safe to run manually or automatically

## UI/UX Design

**Aesthetic:** Black-label noir, zero emojis, premium minimal typography
- Background: #0a0a0a (deep black)
- Text: Georgia serif for headings, Courier for admin console
- Accents: White text, green success ($), red for alerts
- No gradients, no playful UI elements

**Customer Dashboard:**
- Clean, professional, read-only
- Shows business metrics and activity
- Links to detail pages for exploration
- Auto-refresh every 30 seconds
- "Admin Console" button in footer for operators

**Admin Console:**
- Monospace font for operational feel
- Clear metric cards (0-padded numbers)
- Prominent white agent control buttons
- Live execution log with status indicators
- Data tables with hover effects

## System Behavior

### Autopilot Cycle (Every 5 minutes when enabled)
1. **BizDev**: Creates 1-2 random leads with status="new"
2. **Onboarding**: Converts first unqualified lead → Customer + 1-2 tasks
3. **Ops**: Executes first pending task → "done" with cost & profit
4. **Billing**: Creates draft invoice from completed uninvoiced tasks

### Manual Triggering
Operators can click buttons in admin console to run any cycle immediately, useful for testing or accelerating the system.

### Profit Calculation
```
profit_cents = reward_cents - cost_cents
(clamped to ≥ 0)
```

## Configuration & Customization

### Autopilot Control
- **Enable**: Admin console "ENABLE" button OR `POST /admin/autopilot?enabled=true`
- **Disable**: "DISABLE" button OR `POST /admin/autopilot?enabled=false`
- **Status**: Check `/api/settings` endpoint

### Agent Logic Hooks
In `agents.py`, each cycle function has clear comments for:
- **run_bizdev_cycle** (~line 20): Where to add SMTP/SendGrid email sending
- **run_onboarding_cycle** (~line 75): Lead matching & task template logic
- **run_ops_cycle** (~line 105): **Replace simulated result with real OpenAI API call here**
- **run_billing_cycle** (~line 160): Where to add Stripe integration

### Database
- SQLite file: `hossagent.db` (auto-created)
- Schema auto-created on first run via `SQLModel.metadata.create_all()`
- SystemSettings table initialized with id=1 on startup

## Running the System

```bash
# Workflow starts automatically via Replit
python main.py

# Access:
# - Customer Dashboard: https://yourreplit.dev/
# - Admin Console: https://yourreplit.dev/admin
# - API: https://yourreplit.dev/api/leads, etc.
```

## Recent Changes (Dec 2, 2025)

### Completed
✅ Reframed control room → Admin Console at /admin
✅ Added SystemSettings table with autopilot flag
✅ Created async *_cycle functions (BizDev, Onboarding, Ops, Billing)
✅ Implemented autopilot background loop (runs every 5 minutes)
✅ Built customer-facing dashboard at / with live metrics
✅ Added detail pages for customers, leads, invoices
✅ Noir aesthetic maintained across both UIs
✅ Real-time data flow demonstrated with live system running

### Next Steps (Ready for Integration)
- OpenAI integration: Replace `run_ops_cycle` simulated result with real API call
- Email outbound: Add SMTP/SendGrid support to `run_bizdev_cycle`
- Stripe billing: Add `stripe_utils.py` and integrate with `run_billing_cycle`
- Smart Onboarding: Add lead scoring and matching algorithms
- Scheduler: Integrate APScheduler if more complex timing needed

## Future Enhancements
- Multi-user authentication (per-customer login to dashboard)
- Real OpenAI integration in Ops agent
- Stripe billing automation
- Task marketplace (customers post work, agents bid)
- Advanced lead scoring & enrichment
- Email template library
- Webhook support for external integrations
- Analytics & reporting dashboard

