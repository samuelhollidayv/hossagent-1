# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system designed with a noir aesthetic. It features four autonomous agents (BizDev, Onboarding, Ops, Billing), SQLite persistence, and dual UI interfaces: a customer-facing dashboard and an admin console with autopilot control. The system performs self-driving cycles to continuously find leads, convert them into customers, execute tasks autonomously, and generate invoices, all while tracking real-time profit. The vision is to provide a fully automated business operation, from lead generation to billing, with minimal human intervention.

## User Preferences
Not specified.

## System Architecture

### Core Design
HossAgent operates as a FastAPI backend, integrating routes, an autopilot mechanism, and autonomous agents. Data persistence is managed via SQLite, with schema auto-creation on the first run. The system is structured around `SQLModel` for data models and asynchronous cycle functions for agent operations, ensuring idempotency.

### Directory Structure
- `main.py`: FastAPI application, routes, and autopilot loop.
- `models.py`: Defines SQLModel data models (SystemSettings, Lead, Customer, Task, Invoice).
- `database.py`: Handles SQLite setup and schema initialization.
- `agents.py`: Contains the logic for the four autonomous agent cycles.
- `email_utils.py`: Manages email infrastructure (SendGrid, SMTP, DRY_RUN modes).
- `lead_sources.py`: Lead source providers (DummySeed for dev, SearchApi for production).
- `lead_service.py`: Lead generation service with deduplication and logging.
- `templates/`: Stores `dashboard.html` (customer-facing) and `admin_console.html` (operator control).

### Data Models
- **SystemSettings**: Stores global system flags like `autopilot_enabled`.
- **Lead**: Tracks prospecting records through statuses (new, contacted, responded, qualified, dead). Includes `website` and `source` fields for lead source tracking.
- **Customer**: Represents converted leads, including billing information and `stripe_customer_id`.
- **Task**: Units of work, tracking reward, cost, and profit.
- **Invoice**: Aggregates completed tasks for billing purposes.

### Autonomous Agents
The system features four idempotent, asynchronous agents:
1.  **BizDev Cycle**: Generates and contacts new leads.
2.  **Onboarding Cycle**: Converts qualified leads to customers and creates initial tasks.
3.  **Ops Cycle**: Executes pending tasks and calculates profit (with a placeholder for OpenAI integration).
4.  **Billing Cycle**: Aggregates completed tasks and generates draft invoices.

### UI/UX Design
The system employs a "black-label noir" aesthetic, characterized by a deep black background (`#0a0a0a`), premium minimal typography (Georgia serif for headings, Courier for admin console), and stark white/green/red accents. No gradients or playful UI elements are used.

-   **Customer Dashboard (`/`)**: A clean, professional, read-only interface displaying summary metrics, recent work, invoices, and lead pipeline, with auto-refresh every 30 seconds.
-   **Admin Console (`/admin`)**: A control room with a monospace font, metric cards, prominent agent control buttons, a live execution log, and data tables. It allows toggling autopilot and manually triggering agent cycles.

### Autopilot Loop
A background `autopilot_loop` runs every 5 minutes when enabled, executing all agent cycles sequentially. It is designed to be idempotent and robust against errors.

### Lead Generation
HossAgent supports self-driving lead generation, configurable via environment variables (`LEAD_NICHE`, `LEAD_GEOGRAPHY`, `LEAD_MIN_COMPANY_SIZE`, `LEAD_MAX_COMPANY_SIZE`, `MAX_NEW_LEADS_PER_CYCLE`). It can use a `DummySeedLeadSourceProvider` for development or a `SearchApiLeadSourceProvider` for production, integrating with external lead search APIs. Leads are deduplicated by email or company+website to prevent duplicates.

### Email System
Supports `DRY_RUN`, `SENDGRID`, and `SMTP` modes, configurable via `EMAIL_MODE` environment variables. It includes a fallback to `DRY_RUN` if credentials for `SENDGRID` or `SMTP` are missing. The system logs email attempts and integrates email sending into the BizDev cycle.

## External Dependencies

-   **FastAPI**: Web framework for the backend.
-   **SQLModel**: Used for defining data models and ORM functionalities with SQLite.
-   **SQLite**: Primary database for persistence (`hossagent.db`).
-   **SendGrid (Optional)**: For sending real emails (`SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`).
-   **SMTP Server (Optional)**: For sending real emails via SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`).
-   **External Lead Search API (Optional)**: For real lead generation (`LEAD_SEARCH_API_URL`, `LEAD_SEARCH_API_KEY`). Expected to return JSON with company and contact information.