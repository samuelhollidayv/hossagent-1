# HossAgent - Autonomous AI Business Engine

## Overview
A complete noir-aesthetic autonomous business prototype with four autonomous agents (BizDev, Onboarding, Ops, Billing), SQLite persistence, and a sleek black-label control room UI.

The system finds leads, converts them to customers, executes work autonomously, and generates invoices—all with real-time profit tracking.

## Project Architecture

```
hoss-agent/
├── main.py              # FastAPI backend with all routes
├── models.py            # SQLModel data models (Lead, Customer, Task, Invoice)
├── database.py          # SQLite setup and session management
├── agents.py            # Four autonomous agent functions
├── templates/
│   ├── index.html       # Noir landing page
│   └── control_room.html # Admin control center
├── requirements.txt     # Python dependencies
└── hossagent.db        # SQLite database (auto-created)
```

## Data Models

**Lead**: Company prospecting records (new → contacted → responded → qualified → dead)
**Customer**: Converted leads with active subscriptions
**Task**: Work units with reward, cost, and profit tracking
**Invoice**: Billing records aggregating task profits

## Autonomous Agents

1. **BizDev Agent** - Generates realistic leads with corporate names
2. **Onboarding Agent** - Converts responded leads to customers, creates template tasks
3. **Ops Agent** - Picks pending tasks, simulates execution, calculates profit (hooks for real OpenAI API)
4. **Billing Agent** - Aggregates completed tasks per customer, generates draft invoices

## API Endpoints

**Data Retrieval:**
- GET /api/leads
- GET /api/customers
- GET /api/tasks
- GET /api/invoices

**Agent Execution:**
- POST /api/run/bizdev
- POST /api/run/onboarding
- POST /api/run/ops
- POST /api/run/billing

**Pages:**
- GET / → Noir landing page
- GET /control → Control room dashboard

## UI/UX Design

**Aesthetic:** Black-label noir, zero emojis, premium minimal typography
- Landing: Full-width hero, serif headlines, high-contrast white/black
- Control Room: Metrics dashboard, four agent buttons, live data tables
- No purple, no gradients, no playful UI elements

## Configuration & Customization

### Email CTA Links
Update in `templates/index.html`:
- Line 35: "hey@hossagent.io" → your email

### Agent Logic Hooks
In `agents.py`:
- `run_ops_agent()`: Replace simulated OpenAI call with real API (line ~90)
- Future: Add Stripe integration to `run_billing_agent()`

### Task Data
Tasks are created automatically by the Onboarding agent from template descriptions.

## Running the System

```bash
# Workflow automatically starts via Replit
# Access:
# - Landing page: https://yourreplit.dev/
# - Control room: https://yourreplit.dev/control
# - API: https://yourreplit.dev/api/...
```

## Recent Changes
- Complete system rebuild: noir UI, 4-agent architecture (Dec 2, 2025)
- SQLModel ORM with SQLite persistence
- Black-label aesthetic: zero emojis, premium typography
- Fully functional control room with live agent execution
- Real profit accounting: reward - cost = profit

## Future Enhancements
- Real OpenAI integration in Ops agent
- Stripe billing automation
- Multi-agent orchestration
- Task marketplace
- Autonomous revenue loops
