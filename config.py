import os
import re
from datetime import timedelta
from dotenv import load_dotenv

# Load environmental variables from .env (no-op in production where vars are injected)
load_dotenv()

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
IS_VERCEL = bool(os.environ.get("VERCEL"))
IS_PRODUCTION = IS_VERCEL or os.environ.get("FLASK_ENV") == "production"


def _build_database_uri() -> str:
    """
    Determine the correct database URI.

    Priority order:
      1. DATABASE_URL env var  (set this on Vercel / any cloud host)
      2. Explicit Postgres components via PG_* vars
      3. Local SQLite fallback (development only)

    IMPORTANT: SQLAlchemy 2.x requires "postgresql+psycopg2://" not
    the legacy "postgres://" scheme that some providers still emit.
    """
    raw_url = os.environ.get("DATABASE_URL", "")

    if raw_url:
        if raw_url.startswith("postgres://"):
            raw_url = raw_url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif raw_url.startswith("postgresql://") and "+psycopg2" not in raw_url:
            raw_url = raw_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return raw_url

    # Fallback: assemble from individual PG_* variables
    pg_host = os.environ.get("PG_HOST")
    pg_port = os.environ.get("PG_PORT", "5432")
    pg_user = os.environ.get("PG_USER")
    pg_password = os.environ.get("PG_PASSWORD")
    pg_db = os.environ.get("PG_DATABASE")

    if all([pg_host, pg_user, pg_password, pg_db]):
        return (
            f"postgresql+psycopg2://{pg_user}:{pg_password}"
            f"@{pg_host}:{pg_port}/{pg_db}"
        )

    if IS_PRODUCTION:
        raise RuntimeError(
            "No database configured for production. "
            "Set DATABASE_URL (or PG_HOST / PG_USER / PG_PASSWORD / PG_DATABASE) "
            "in your Vercel environment variables."
        )

    # Local development: SQLite
    base_dir = os.path.abspath(os.path.dirname(__file__))
    sqlite_path = os.path.join(base_dir, "instance", "chat_assistant.db")
    return f"sqlite:///{sqlite_path}"


# ---------------------------------------------------------------------------
# Password complexity
# ---------------------------------------------------------------------------
PASSWORD_MIN_LENGTH = 8
PASSWORD_RULES = [
    (r"[A-Z]", "at least one uppercase letter"),
    (r"[a-z]", "at least one lowercase letter"),
    (r"[0-9]", "at least one digit"),
]


def validate_password(password: str) -> str | None:
    """Return an error message if password doesn't meet complexity requirements, else None."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
    for pattern, description in PASSWORD_RULES:
        if not re.search(pattern, password):
            return f"Password must contain {description}"
    return None


class Config:
    """Central Flask application configuration."""

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    SECRET_KEY: str = os.environ.get("SECRET_KEY") or os.urandom(32).hex()

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    SQLALCHEMY_DATABASE_URI: str = _build_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    SQLALCHEMY_ENGINE_OPTIONS: dict = (
        {
            "pool_pre_ping": True,
            "pool_recycle": 300,
            "pool_size": 5,
            "max_overflow": 10,
        }
        if IS_PRODUCTION
        else {}
    )

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------
    UPLOAD_FOLDER: str = (
        "/tmp/uploads"
        if IS_VERCEL
        else os.path.join(os.path.abspath(os.path.dirname(__file__)), "uploads")
    )
    MAX_CONTENT_LENGTH: int = 10 * 1024 * 1024  # 10 MB
    ALLOWED_EXTENSIONS: set = {"txt", "pdf", "docx", "png", "jpg", "jpeg", "gif"}

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------
    SESSION_COOKIE_SECURE: bool = (
        os.environ.get("SESSION_COOKIE_SECURE", "False").lower() in ("true", "1")
    )
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)

    # ------------------------------------------------------------------
    # Groq / LLM
    # ------------------------------------------------------------------
    GROQ_API_KEY: str | None = os.environ.get("GROQ_API_KEY")
    GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    # ------------------------------------------------------------------
    # Context limits
    # ------------------------------------------------------------------
    MAX_PROMPT_CHARS: int = 4_000
    MAX_DOC_CHARS: int = 12_000
    MAX_CONTEXT_MESSAGES: int = 15

    # ------------------------------------------------------------------
    # Rate limits (Flask-Limiter format strings)
    # ------------------------------------------------------------------
    RATE_LIMIT_LOGIN: str = "5 per minute"
    RATE_LIMIT_REGISTER: str = "3 per minute"
    RATE_LIMIT_UPLOAD: str = "10 per minute"
    RATE_LIMIT_MESSAGE: str = "20 per minute"
    RATE_LIMIT_DEFAULT: str = "60 per minute"