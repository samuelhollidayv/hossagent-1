from sqlmodel import SQLModel, create_engine, Session, select
import os

DATABASE_URL = "sqlite:///./hossagent.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables():
    """Create database tables if they don't exist and initialize SystemSettings."""
    SQLModel.metadata.create_all(engine)
    
    # Ensure SystemSettings has exactly one row with id=1
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
