import os
from dotenv import load_dotenv

# Load environmental variables from .env
load_dotenv()

class Config:
    """Flask application configuration class"""
    # Key security settings
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.urandom(24).hex()
    
    # Database Settings
    # Fallback to local SQLite instance db
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'chat_assistant.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # File upload configurations
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB maximum upload size
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'docx', 'png', 'jpg', 'jpeg', 'gif'}
    
    # Session cookie protections
    # Set SESSION_COOKIE_SECURE=True in production (HTTPS)
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    
    # SambaNova Configs
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
    GROQ_BASE_URL = "https://api.groq.com/openai/v1"

    # AI Model
    GROQ_MODEL= "llama-3.3-70b-versatile"
    # Prompt and File context bounds
    MAX_PROMPT_CHARS = 4000
    MAX_DOC_CHARS = 12000

