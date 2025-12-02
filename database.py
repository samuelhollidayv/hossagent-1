from sqlmodel import SQLModel, create_engine, Session
import os

DATABASE_URL = "sqlite:///./hossagent.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables():
    """Create database tables if they don't exist."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """Get a database session."""
    with Session(engine) as session:
        yield session
