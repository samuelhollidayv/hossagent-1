"""
Authentication Utilities for HossAgent.

Handles:
- Password hashing with bcrypt
- Session management with signed cookies
- Admin authentication
- Customer authentication

Environment Variables:
  SESSION_SECRET - Secret key for signing session cookies (required)
  ADMIN_PASSWORD - Password for admin console access (default: hoss2024)
"""
import os
import secrets
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, Tuple
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlmodel import Session, select

from models import Customer


SESSION_COOKIE_NAME = "hossagent_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days in seconds
ADMIN_COOKIE_NAME = "hossagent_admin"
ADMIN_SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours


def get_session_secret() -> str:
    """Get or generate session secret."""
    secret = os.getenv("SESSION_SECRET")
    if not secret:
        secret = secrets.token_hex(32)
        os.environ["SESSION_SECRET"] = secret
        print("[AUTH][WARNING] No SESSION_SECRET set - using generated secret (sessions won't persist across restarts)")
    return secret


def get_admin_password() -> str:
    """Get admin password from environment."""
    return os.getenv("ADMIN_PASSWORD", "hoss2024")


def get_serializer() -> URLSafeTimedSerializer:
    """Get the session serializer."""
    return URLSafeTimedSerializer(get_session_secret())


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False


def create_customer_session(customer_id: int) -> str:
    """Create a signed session token for a customer."""
    serializer = get_serializer()
    data = {
        "customer_id": customer_id,
        "type": "customer",
        "created_at": datetime.utcnow().isoformat()
    }
    return serializer.dumps(data)


def verify_customer_session(token: str) -> Optional[int]:
    """
    Verify a customer session token.
    
    Returns:
        customer_id if valid, None if invalid or expired
    """
    if not token:
        return None
    
    serializer = get_serializer()
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        if data.get("type") == "customer":
            return data.get("customer_id")
    except (BadSignature, SignatureExpired):
        pass
    return None


def create_admin_session() -> str:
    """Create a signed session token for admin."""
    serializer = get_serializer()
    data = {
        "type": "admin",
        "created_at": datetime.utcnow().isoformat()
    }
    return serializer.dumps(data)


def verify_admin_session(token: str) -> bool:
    """
    Verify an admin session token.
    
    Returns:
        True if valid, False if invalid or expired
    """
    if not token:
        return False
    
    serializer = get_serializer()
    try:
        data = serializer.loads(token, max_age=ADMIN_SESSION_MAX_AGE)
        return data.get("type") == "admin"
    except (BadSignature, SignatureExpired):
        return False


def authenticate_customer(
    db_session: Session,
    email: str,
    password: str
) -> Tuple[Optional[Customer], Optional[str]]:
    """
    Authenticate a customer by email and password.
    
    Returns:
        (customer, error_message)
    """
    customer = db_session.exec(
        select(Customer).where(Customer.contact_email == email.lower().strip())
    ).first()
    
    if not customer:
        return None, "No account found with that email"
    
    if not customer.password_hash:
        return None, "Account not set up for login - please contact support"
    
    if not verify_password(password, customer.password_hash):
        return None, "Incorrect password"
    
    return customer, None


def generate_public_token() -> str:
    """Generate a secure public token for portal access."""
    return secrets.token_urlsafe(16)


def get_customer_from_session(
    db_session: Session,
    session_token: Optional[str]
) -> Optional[Customer]:
    """
    Get customer from session token.
    
    Returns:
        Customer if valid session, None otherwise
    """
    customer_id = verify_customer_session(session_token)
    if not customer_id:
        return None
    
    return db_session.exec(
        select(Customer).where(Customer.id == customer_id)
    ).first()


def get_customer_from_token(
    db_session: Session,
    public_token: str
) -> Optional[Customer]:
    """
    Get customer from public token.
    
    Returns:
        Customer if valid token, None otherwise
    """
    if not public_token or len(public_token) < 10:
        return None
    
    return db_session.exec(
        select(Customer).where(Customer.public_token == public_token)
    ).first()
