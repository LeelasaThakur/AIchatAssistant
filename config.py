import os
from dotenv import load_dotenv

# Load environmental variables from .env (no-op in production where vars are injected)
load_dotenv()

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
# VERCEL env var is automatically set to "1" by the Vercel runtime.
IS_VERCEL = bool(os.environ.get("VERCEL"))
# Optional explicit override: set FLASK_ENV=production in any environment.
IS_PRODUCTION = IS_VERCEL or os.environ.get("FLASK_ENV") == "production"


def _build_database_uri() -> str:
    """
    Determine the correct database URI.

    Priority order:
      1. DATABASE_URL env var  (set this on Vercel / any cloud host)
      2. Explicit Postgres components via PG_* vars
      3. Local SQLite fallback (development only – never used on Vercel)

    Vercel injects DATABASE_URL automatically when a Postgres integration
    (e.g. Vercel Postgres / Neon) is attached to the project.

    IMPORTANT: SQLAlchemy 2.x requires "postgresql+psycopg2://" not
    the legacy "postgres://" scheme that some providers still emit.
    """
    raw_url = os.environ.get("DATABASE_URL", "")

    if raw_url:
        # Heroku / Neon / Supabase historically emit "postgres://…"
        # SQLAlchemy 2.x only accepts "postgresql+psycopg2://…"
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


class Config:
    """Central Flask application configuration."""

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    SECRET_KEY: str = os.environ.get("SECRET_KEY") or os.urandom(24).hex()

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    SQLALCHEMY_DATABASE_URI: str = _build_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # Connection-pool settings tuned for serverless (each invocation is
    # short-lived; we want connections to be recycled quickly).
    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "pool_pre_ping": True,       # drop stale connections silently
        "pool_recycle": 300,         # recycle every 5 minutes
        "pool_size": 5,
        "max_overflow": 10,
    } if IS_PRODUCTION else {}

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------
    # On Vercel, /tmp is the ONLY writable directory.
    # On local dev, store uploads next to the project.
    UPLOAD_FOLDER: str = (
        "/tmp/uploads"
        if IS_VERCEL
        else os.path.join(os.path.abspath(os.path.dirname(__file__)), "uploads")
    )
    MAX_CONTENT_LENGTH: int = 10 * 1024 * 1024  # 10 MB
    ALLOWED_EXTENSIONS: set = {"txt", "pdf", "docx", "png", "jpg", "jpeg", "gif"}

    # ------------------------------------------------------------------
    # Session cookies
    # ------------------------------------------------------------------
    SESSION_COOKIE_SECURE: bool = (
        os.environ.get("SESSION_COOKIE_SECURE", "False").lower() in ("true", "1")
    )
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"

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