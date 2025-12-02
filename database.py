from sqlmodel import SQLModel, create_engine, Session, select
import sqlite3
import os

DATABASE_URL = "sqlite:///./hossagent.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def _run_migrations():
    """
    Run schema migrations for existing databases.
    This ensures new columns are added without losing data.
    """
    import secrets
    
    conn = sqlite3.connect('./hossagent.db')
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(lead)")
    lead_columns = {row[1] for row in cursor.fetchall()}
    
    if 'website' not in lead_columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN website TEXT')
            print("✓ Migration: Added 'website' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    if 'source' not in lead_columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN source TEXT')
            print("✓ Migration: Added 'source' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    cursor.execute("PRAGMA table_info(customer)")
    customer_columns = {row[1] for row in cursor.fetchall()}
    
    if 'public_token' not in customer_columns:
        try:
            cursor.execute('ALTER TABLE customer ADD COLUMN public_token TEXT')
            print("✓ Migration: Added 'public_token' column to customer table")
        except sqlite3.OperationalError:
            pass
    
    cursor.execute("PRAGMA table_info(invoice)")
    invoice_columns = {row[1] for row in cursor.fetchall()}
    
    if 'payment_url' not in invoice_columns:
        try:
            cursor.execute('ALTER TABLE invoice ADD COLUMN payment_url TEXT')
            print("✓ Migration: Added 'payment_url' column to invoice table")
        except sqlite3.OperationalError:
            pass
    
    if 'stripe_payment_id' not in invoice_columns:
        try:
            cursor.execute('ALTER TABLE invoice ADD COLUMN stripe_payment_id TEXT')
            print("✓ Migration: Added 'stripe_payment_id' column to invoice table")
        except sqlite3.OperationalError:
            pass
    
    conn.commit()
    
    cursor.execute("SELECT id FROM customer WHERE public_token IS NULL")
    customers_without_token = cursor.fetchall()
    for (customer_id,) in customers_without_token:
        token = secrets.token_urlsafe(16)
        cursor.execute("UPDATE customer SET public_token = ? WHERE id = ?", (token, customer_id))
        print(f"✓ Migration: Generated public_token for customer {customer_id}")
    
    conn.commit()
    conn.close()


def create_db_and_tables():
    """Create database tables if they don't exist and initialize SystemSettings."""
    SQLModel.metadata.create_all(engine)
    
    _run_migrations()
    
    from models import SystemSettings
    with Session(engine) as session:
        existing = session.exec(select(SystemSettings).where(SystemSettings.id == 1)).first()
        if not existing:
            settings = SystemSettings(id=1, autopilot_enabled=True)
            session.add(settings)
            session.commit()
            print("✓ SystemSettings initialized: autopilot_enabled=True")


def get_session():
    """Get a database session."""
    with Session(engine) as session:
        yield session
