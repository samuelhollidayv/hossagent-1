from sqlmodel import SQLModel, create_engine, Session, select
import os

DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = DATABASE_URL is not None and "postgresql" in DATABASE_URL

if DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
    )
    print(f"[DATABASE] Using PostgreSQL (pool_pre_ping=True, pool_recycle=300s)")
else:
    DATABASE_URL = "sqlite:///./hossagent.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
    print(f"[DATABASE] Using SQLite (development only)")


def _run_sqlite_migrations():
    """
    Run schema migrations for existing SQLite databases.
    This ensures new columns are added without losing data.
    Only runs for SQLite - PostgreSQL uses fresh schema from SQLModel.
    """
    if IS_POSTGRES:
        return
    
    import sqlite3
    import secrets
    
    conn = sqlite3.connect('./hossagent.db')
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(lead)")
    lead_columns = {row[1] for row in cursor.fetchall()}
    
    if 'website' not in lead_columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN website TEXT')
            print("[MIGRATION] Added 'website' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    if 'source' not in lead_columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN source TEXT')
            print("[MIGRATION] Added 'source' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    cursor.execute("PRAGMA table_info(customer)")
    customer_columns = {row[1] for row in cursor.fetchall()}
    
    if 'public_token' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN public_token TEXT')
            print("[MIGRATION] Added 'public_token' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'trial_start_at' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN trial_start_at TEXT')
            print("[MIGRATION] Added 'trial_start_at' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'trial_end_at' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN trial_end_at TEXT')
            print("[MIGRATION] Added 'trial_end_at' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'subscription_status' not in customer_columns:
        try:
            cursor.execute("ALTER TABLE customer ADD COLUMN subscription_status TEXT DEFAULT 'none'")
            print("[MIGRATION] Added 'subscription_status' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'stripe_subscription_id' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN stripe_subscription_id TEXT')
            print("[MIGRATION] Added 'stripe_subscription_id' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'tasks_this_period' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN tasks_this_period INTEGER DEFAULT 0')
            print("[MIGRATION] Added 'tasks_this_period' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'leads_this_period' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN leads_this_period INTEGER DEFAULT 0')
            print("[MIGRATION] Added 'leads_this_period' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'billing_method' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN billing_method TEXT')
            print("[MIGRATION] Added 'billing_method' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'cancelled_at_period_end' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN cancelled_at_period_end INTEGER DEFAULT 0')
            print("[MIGRATION] Added 'cancelled_at_period_end' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'cancellation_effective_at' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN cancellation_effective_at TEXT')
            print("[MIGRATION] Added 'cancellation_effective_at' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'outreach_mode' not in customer_columns:
        try:
            cursor.execute("ALTER TABLE customer ADD COLUMN outreach_mode TEXT DEFAULT 'AUTO'")
            print("[MIGRATION] Added 'outreach_mode' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    if 'last_contact_summary' not in lead_columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN last_contact_summary TEXT')
            print("[MIGRATION] Added 'last_contact_summary' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    if 'next_step' not in lead_columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN next_step TEXT')
            print("[MIGRATION] Added 'next_step' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    if 'next_step_owner' not in lead_columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN next_step_owner TEXT')
            print("[MIGRATION] Added 'next_step_owner' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    cursor.execute("PRAGMA table_info(leadevent)")
    leadevent_columns = {row[1] for row in cursor.fetchall()}
    
    if 'last_contact_at' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN last_contact_at TEXT')
            print("[MIGRATION] Added 'last_contact_at' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'last_contact_summary' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN last_contact_summary TEXT')
            print("[MIGRATION] Added 'last_contact_summary' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'next_step' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN next_step TEXT')
            print("[MIGRATION] Added 'next_step' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'next_step_owner' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN next_step_owner TEXT')
            print("[MIGRATION] Added 'next_step_owner' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'enrichment_attempts' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN enrichment_attempts INTEGER DEFAULT 0')
            print("[MIGRATION] Added 'enrichment_attempts' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'last_enrichment_at' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN last_enrichment_at TEXT')
            print("[MIGRATION] Added 'last_enrichment_at' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'social_facebook' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN social_facebook TEXT')
            print("[MIGRATION] Added 'social_facebook' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'social_instagram' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN social_instagram TEXT')
            print("[MIGRATION] Added 'social_instagram' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'social_linkedin' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN social_linkedin TEXT')
            print("[MIGRATION] Added 'social_linkedin' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    if 'social_twitter' not in leadevent_columns:
        try:
            cursor.execute('ALTER TABLE leadevent ADD COLUMN social_twitter TEXT')
            print("[MIGRATION] Added 'social_twitter' column to leadevent table")
        except sqlite3.OperationalError:
            pass
    
    cursor.execute("PRAGMA table_info(invoice)")
    invoice_columns = {row[1] for row in cursor.fetchall()}
    
    if 'payment_url' not in invoice_columns:
        try:
            cursor.execute('ALTER TABLE invoice ADD COLUMN payment_url TEXT')
            print("[MIGRATION] Added 'payment_url' column to invoice table")
        except sqlite3.OperationalError:
            pass
    
    if 'stripe_payment_id' not in invoice_columns:
        try:
            cursor.execute('ALTER TABLE invoice ADD COLUMN stripe_payment_id TEXT')
            print("[MIGRATION] Added 'stripe_payment_id' column to invoice table")
        except sqlite3.OperationalError:
            pass
    
    conn.commit()
    
    cursor.execute("SELECT id FROM customer WHERE public_token IS NULL")
    customers_without_token = cursor.fetchall()
    for (customer_id,) in customers_without_token:
        import secrets as sec
        token = sec.token_urlsafe(16)
        cursor.execute("UPDATE customer SET public_token = ? WHERE id = ?", (token, customer_id))
        print(f"[MIGRATION] Generated public_token for customer {customer_id}")
    
    cursor.execute("SELECT id, plan FROM customer WHERE plan = 'starter' OR plan IS NULL")
    legacy_customers = cursor.fetchall()
    for (customer_id, plan) in legacy_customers:
        cursor.execute("UPDATE customer SET plan = 'paid', subscription_status = 'active' WHERE id = ?", (customer_id,))
        print(f"[MIGRATION] Upgraded legacy customer {customer_id} to paid plan (grandfathered)")
    
    conn.commit()
    conn.close()


def create_db_and_tables():
    """Create database tables if they don't exist and initialize SystemSettings."""
    SQLModel.metadata.create_all(engine)
    
    _run_sqlite_migrations()
    
    from models import SystemSettings
    with Session(engine) as session:
        existing = session.exec(select(SystemSettings).where(SystemSettings.id == 1)).first()
        if not existing:
            settings = SystemSettings(id=1, autopilot_enabled=True)
            session.add(settings)
            session.commit()
            print("[STARTUP] SystemSettings initialized: autopilot_enabled=True")


def get_session():
    """Get a database session."""
    with Session(engine) as session:
        yield session
