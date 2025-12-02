# HossAgent - Autonomous AI Business Engine

## Overview
HossAgent is an autonomous AI business system featuring a noir aesthetic. It orchestrates four AI agents (BizDev, Onboarding, Ops, Billing) to autonomously find leads, convert them into customers, execute tasks, and generate Stripe-powered invoices, all while tracking real-time profit. The system provides customer-facing and admin interfaces, including a customer portal for invoice management.

## User Preferences
Not specified.

## System Architecture
HossAgent is built on a FastAPI backend, utilizing `SQLModel` for data persistence with SQLite, ensuring schema auto-creation and migration. The system's core functionality revolves around asynchronous agent cycles that operate idempotently.

**UI/UX Design:**
The system adopts a "black-label noir" aesthetic with deep black backgrounds (`#0a0a0a`), Georgia serif for the customer UI, Courier monospace for the admin console, and stark white/green/red accents. Gradients and emojis are explicitly avoided.
- **Customer Dashboard (`/`)**: Displays metrics, recent work, invoices, and lead pipeline.
- **Admin Console (`/admin`)**: Provides control over agents, system status, and data tables.
- **Customer Portal (`/portal/<token>`)**: A client-facing interface for viewing and paying invoices via Stripe.

**Core Features:**
- **Autonomous Agents**:
    - **BizDev Cycle**: Prospects new leads via outreach emails.
    - **Onboarding Cycle**: Converts qualified leads into customers and initiates tasks.
    - **Ops Cycle**: Executes tasks, calculating reward, cost, and profit.
    - **Billing Cycle**: Generates invoices and integrates with Stripe for payment links.
- **Data Models**: `SystemSettings`, `Lead`, `Customer`, `Task`, `Invoice` manage system state and business entities.
- **Email Infrastructure**: Supports SendGrid and SMTP with robust throttling and DRY_RUN mode for safety.
- **Lead Generation**: Configurable lead sourcing with domain-based deduplication.
- **Stripe Integration**: Handles invoice payment link generation, webhook processing, and payment status updates.

**Autopilot Flow:**
When enabled, the autopilot runs every 5 minutes, executing lead generation, BizDev, Onboarding, Ops, and Billing cycles sequentially.

## External Dependencies
- **FastAPI**: Web framework for the backend.
- **SQLModel**: ORM for data modeling and interaction with SQLite.
- **SQLite**: Database for data persistence.
- **SendGrid / SMTP**: Optional email service providers for outreach.
- **Stripe**: Optional payment gateway for invoice processing and payment links.
- **External Lead API**: Optional third-party service for lead sourcing.