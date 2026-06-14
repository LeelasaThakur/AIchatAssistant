from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate

db = SQLAlchemy()
bcrypt = Bcrypt()
csrf = CSRFProtect()
migrate = Migrate()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],              # no global default — apply per-route
    storage_uri="memory://",        # in-process; swap to redis:// in production
)