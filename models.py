"""
Database models for Whisper API STT application.
"""
import os
import uuid
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base

# Database setup (SQLite by default; path configurable)
DB_PATH = os.getenv("DB_PATH", "/data/app.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def generate_uuid():
    """Generate a new UUID string."""
    return str(uuid.uuid4())


class Setting(Base):
    """Application settings stored in database."""
    __tablename__ = "settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    value_type = Column(String(20), default="string")  # string, boolean, integer, json
    category = Column(String(50), nullable=True)  # auth, api, system
    updated_at = Column(DateTime, nullable=True)
    updated_by = Column(String(255), nullable=True)


class User(Base):
    """User accounts for authentication."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=True)  # NULL for non-local users
    source = Column(String(20), nullable=False, default="local")  # local, http-header, oidc
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    preferences = Column(Text, nullable=True)  # JSON: {theme, language}
    oidc_subject = Column(String(255), nullable=True)  # OIDC subject claim
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)


class Session(Base):
    """User sessions for authentication."""
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), nullable=False)  # References User.id
    token_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)


class ApiKey(Base):
    """API keys for programmatic access."""
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False)
    key_hash = Column(String(255), nullable=False)  # bcrypt hash
    key_prefix = Column(String(12), nullable=False)  # First chars for display (e.g., "wsk_abc...")
    is_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_by = Column(String(255), nullable=True)  # User email who created it
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # NULL = never expires
    usage_count = Column(Integer, default=0)


class TranscriptionJob(Base):
    """Transcription job records."""
    __tablename__ = "transcription_jobs"

    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), nullable=True)  # References User.id (NULL for API/anonymous)
    user_email = Column(String(255), nullable=True)  # Denormalized for display
    source_type = Column(String(20), nullable=True)  # file|url
    source_url = Column(Text, nullable=True)
    original_filename = Column(String(255), nullable=True)
    method = Column(String(20), nullable=False)  # local|cloud
    local_model_size = Column(String(20), nullable=True)
    cloud_model = Column(String(50), nullable=True)
    with_timestamps = Column(Boolean, default=False)
    status = Column(String(20), default="queued")  # queued|running|completed|error|cancelled
    percent = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    result_text = Column(Text, nullable=True)
    result_segments = Column(Text, nullable=True)  # JSON string
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)


def init_db():
    """Initialize database schema."""
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    except Exception:
        pass
    Base.metadata.create_all(engine)


def get_session():
    """Get a new database session."""
    return SessionLocal()
