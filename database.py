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
    conn = sqlite3.connect('./hossagent.db')
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(lead)")
    columns = {row[1] for row in cursor.fetchall()}
    
    if 'website' not in columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN website TEXT')
            print("✓ Migration: Added 'website' column to lead table")
        except sqlite3.OperationalError:
            pass
    
    if 'source' not in columns:
        try:
            cursor.execute('ALTER TABLE lead ADD COLUMN source TEXT')
            print("✓ Migration: Added 'source' column to lead table")
        except sqlite3.OperationalError:
            pass
    
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
