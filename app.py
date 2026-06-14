import os
import uuid
import logging
import threading
import time as _time
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, make_response
from werkzeug.utils import secure_filename

from groq import Groq

from config import Config, IS_VERCEL, IS_PRODUCTION, validate_password
from extensions import db, bcrypt, csrf, limiter, migrate
from models import User, Chat, Message
from document_parser import allowed_file, extract_text_from_file, validate_mime_type

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config.from_object(Config)

# Initialise extensions
db.init_app(app)
bcrypt.init_app(app)
csrf.init_app(app)
limiter.init_app(app)
migrate.init_app(app, db)

# ---------------------------------------------------------------------------
# Upload directory
# ---------------------------------------------------------------------------
try:
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
except OSError as exc:
    logging.warning("Could not create upload folder %s: %s", app.config["UPLOAD_FOLDER"], exc)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)

if not IS_VERCEL and not app.debug:
    try:
        from logging.handlers import RotatingFileHandler
        os.makedirs("logs", exist_ok=True)
        file_handler = RotatingFileHandler(
            "logs/chat_assistant.log", maxBytes=5 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]")
        )
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
    except OSError as exc:
        app.logger.warning("Could not set up file logging: %s", exc)

# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------
groq_client: Groq | None = None

if app.config["GROQ_API_KEY"]:
    try:
        groq_client = Groq(api_key=app.config["GROQ_API_KEY"])
        app.logger.info("Groq client initialised successfully.")
    except Exception as exc:
        app.logger.error("Failed to initialise Groq client: %s", exc)
else:
    app.logger.warning("GROQ_API_KEY is not set. Chat requests will fail.")

# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------
with app.app_context():
    try:
        db.create_all()
        app.logger.info("Database tables initialised/verified.")
    except Exception as exc:
        app.logger.error("Error creating database tables: %s", exc)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

@app.after_request
def set_security_headers(response):
    """Inject production-grade security headers on every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # CSP: allow CDN for fonts, markdown lib, syntax highlighting
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["Content-Security-Policy"] = csp

    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_current_user() -> User | None:
    """Return the logged-in User from the session, or None."""
    user_id = session.get("user_id")
    if user_id:
        return db.session.get(User, user_id)
    return None


def login_required_api(f):
    """Decorator that enforces authentication for JSON API endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Authentication required", "success": False}), 401
        return f(*args, **kwargs)
    return decorated


def call_llm_with_retry(messages: list, model: str, max_retries: int = 3, base_delay: float = 1.0) -> str:
    """Call the Groq chat completion API with exponential-backoff retries."""
    if not groq_client:
        raise ValueError("AI service is not configured.")

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            app.logger.info("Groq API call attempt %d/%d", attempt + 1, max_retries)
            response = groq_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                top_p=0.9,
            )
            return response.choices[0].message.content
        except Exception as exc:
            last_exc = exc
            app.logger.warning("Groq attempt %d failed: %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                _time.sleep(base_delay * (2 ** attempt))

    # Classify the error for the client
    exc_name = type(last_exc).__name__.lower()
    if "auth" in exc_name or "permission" in exc_name:
        raise ValueError("AI service authentication failed. Please contact support.")
    elif "rate" in exc_name:
        raise ValueError("AI service is temporarily busy. Please try again in a moment.")
    elif "timeout" in exc_name:
        raise ValueError("AI service timed out. Please try a shorter message.")
    else:
        raise ValueError("AI service is currently unavailable. Please try again later.")


# ---------------------------------------------------------------------------
# Orphaned file cleanup (background daemon)
# ---------------------------------------------------------------------------

def _cleanup_orphaned_files():
    """Delete files in UPLOAD_FOLDER that are not referenced by any Message."""
    with app.app_context():
        try:
            upload_dir = app.config["UPLOAD_FOLDER"]
            if not os.path.isdir(upload_dir):
                return

            referenced = set()
            for msg in Message.query.filter(Message.file_path.isnot(None)).all():
                if msg.file_path:
                    referenced.add(os.path.basename(msg.file_path))

            removed = 0
            for fname in os.listdir(upload_dir):
                fpath = os.path.join(upload_dir, fname)
                if os.path.isfile(fpath) and fname not in referenced:
                    # Only delete files older than 1 hour
                    age = _time.time() - os.path.getmtime(fpath)
                    if age > 3600:
                        try:
                            os.remove(fpath)
                            removed += 1
                        except OSError:
                            pass

            if removed:
                app.logger.info("Cleanup: removed %d orphaned files", removed)
        except Exception as exc:
            app.logger.error("Cleanup error: %s", exc)


def _start_cleanup_scheduler():
    """Run orphaned file cleanup every 6 hours in a daemon thread."""
    def _loop():
        while True:
            _time.sleep(6 * 3600)  # 6 hours
            _cleanup_orphaned_files()

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    app.logger.info("Orphaned file cleanup scheduler started.")


# Start cleanup only in non-Vercel environments (Vercel has ephemeral /tmp)
if not IS_VERCEL:
    _start_cleanup_scheduler()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


# --- Auth ---

@app.route("/register", methods=["POST"])
@limiter.limit(Config.RATE_LIMIT_REGISTER)
def register():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request", "success": False}), 400

        username = data.get("username", "").strip()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not username or not email or not password:
            return jsonify({"error": "All fields are required", "success": False}), 400
        if len(username) < 3 or len(username) > 30:
            return jsonify({"error": "Username must be 3-30 characters", "success": False}), 400

        # Password complexity check
        pwd_error = validate_password(password)
        if pwd_error:
            return jsonify({"error": pwd_error, "success": False}), 400

        # Generic error to prevent user enumeration
        if User.query.filter((User.username == username) | (User.email == email)).first():
            return jsonify({"error": "An account with these credentials already exists", "success": False}), 400

        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        csrf_token = session.get("csrf_token")
        session.clear()
        if csrf_token:
            session["csrf_token"] = csrf_token
            
        session["user_id"] = new_user.id
        session.permanent = True

        app.logger.info("Registered user: %s", username)
        return jsonify({"success": True, "message": "Account created successfully", "user": new_user.to_dict()})

    except Exception as exc:
        db.session.rollback()
        app.logger.error("Registration error: %s", exc)
        return jsonify({"error": "Registration failed. Please try again.", "success": False}), 500


@app.route("/login", methods=["POST"])
@limiter.limit(Config.RATE_LIMIT_LOGIN)
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request", "success": False}), 400

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({"error": "Username and password are required", "success": False}), 400

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            csrf_token = session.get("csrf_token")
            session.clear()
            if csrf_token:
                session["csrf_token"] = csrf_token
                
            session["user_id"] = user.id
            session.permanent = True
            app.logger.info("Logged in user: %s", username)
            return jsonify({"success": True, "message": "Login successful", "user": user.to_dict()})

        # Generic error — never reveal whether username exists
        return jsonify({"error": "Invalid credentials", "success": False}), 401

    except Exception as exc:
        app.logger.error("Login error: %s", exc)
        return jsonify({"error": "Login failed. Please try again.", "success": False}), 500


@app.route("/logout", methods=["POST"])
def logout():
    user = get_current_user()
    username = user.username if user else "unknown"
    csrf_token = session.get("csrf_token")
    session.clear()
    if csrf_token:
        session["csrf_token"] = csrf_token
    app.logger.info("Logged out user: %s", username)
    return jsonify({"success": True, "message": "Logged out successfully"})


@app.route("/api/me", methods=["GET"])
def get_me():
    user = get_current_user()
    if user:
        return jsonify({"logged_in": True, "user": user.to_dict()})
    return jsonify({"logged_in": False})


@app.route("/api/settings", methods=["POST"])
@login_required_api
def update_settings():
    try:
        user = get_current_user()
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request", "success": False}), 400
        if "dark_mode" in data:
            user.dark_mode = bool(data["dark_mode"])
            db.session.commit()
            return jsonify({"success": True, "dark_mode": user.dark_mode})
        return jsonify({"error": "No valid settings provided", "success": False}), 400
    except Exception as exc:
        db.session.rollback()
        app.logger.error("Settings update error: %s", exc)
        return jsonify({"error": "Failed to update settings", "success": False}), 500


# --- Chats ---

@app.route("/api/chats", methods=["GET"])
@login_required_api
def get_chats():
    user = get_current_user()
    chats = (
        Chat.query
        .filter_by(user_id=user.id)
        .order_by(Chat.pinned.desc(), Chat.updated_at.desc())
        .all()
    )
    return jsonify({"success": True, "chats": [c.to_dict() for c in chats]})


@app.route("/api/chats/search", methods=["GET"])
@login_required_api
def search_chats():
    user = get_current_user()
    query = request.args.get("q", "").strip()
    if not query or len(query) < 2:
        return jsonify({"success": True, "chats": []})

    search_pattern = f"%{query}%"
    chats = (
        Chat.query
        .filter(Chat.user_id == user.id, Chat.title.ilike(search_pattern))
        .order_by(Chat.updated_at.desc())
        .limit(20)
        .all()
    )
    return jsonify({"success": True, "chats": [c.to_dict() for c in chats]})


@app.route("/api/chats", methods=["POST"])
@login_required_api
def create_chat():
    try:
        user = get_current_user()
        chat_id = str(uuid.uuid4())
        new_chat = Chat(id=chat_id, user_id=user.id, title="New Chat")
        db.session.add(new_chat)
        db.session.commit()
        app.logger.info("Created chat %s for user %s", chat_id, user.username)
        return jsonify({"success": True, "chat": new_chat.to_dict()})
    except Exception as exc:
        db.session.rollback()
        app.logger.error("Chat creation error: %s", exc)
        return jsonify({"error": "Failed to create chat", "success": False}), 500


@app.route("/api/chats/<chat_id>", methods=["GET"])
@login_required_api
def get_chat_messages(chat_id):
    user = get_current_user()
    chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
    if not chat:
        return jsonify({"error": "Chat not found", "success": False}), 404

    messages = (
        chat.messages
        .order_by(Message.timestamp.asc())
        .all()
    )
    return jsonify({
        "success": True,
        "chat": chat.to_dict(),
        "messages": [m.to_dict() for m in messages],
    })


@app.route("/api/chats/<chat_id>", methods=["PUT"])
@login_required_api
def update_chat(chat_id):
    """Rename or pin/unpin a chat."""
    try:
        user = get_current_user()
        chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
        if not chat:
            return jsonify({"error": "Chat not found", "success": False}), 404

        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request", "success": False}), 400

        if "title" in data:
            new_title = str(data["title"]).strip()
            if not new_title or len(new_title) > 150:
                return jsonify({"error": "Title must be 1-150 characters", "success": False}), 400
            chat.title = new_title

        if "pinned" in data:
            chat.pinned = bool(data["pinned"])

        db.session.commit()
        return jsonify({"success": True, "chat": chat.to_dict()})
    except Exception as exc:
        db.session.rollback()
        app.logger.error("Chat update error: %s", exc)
        return jsonify({"error": "Failed to update chat", "success": False}), 500


@app.route("/api/chats/<chat_id>", methods=["DELETE"])
@login_required_api
def delete_chat(chat_id):
    try:
        user = get_current_user()
        chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
        if not chat:
            return jsonify({"error": "Chat not found", "success": False}), 404

        # Best-effort cleanup of uploaded files
        for message in chat.messages.filter(Message.file_path.isnot(None)).all():
            if message.file_path and os.path.exists(message.file_path):
                try:
                    os.remove(message.file_path)
                except OSError as exc:
                    app.logger.warning("Could not delete file %s: %s", message.file_path, exc)

        db.session.delete(chat)
        db.session.commit()
        app.logger.info("Deleted chat %s", chat_id)
        return jsonify({"success": True, "message": "Chat deleted"})
    except Exception as exc:
        db.session.rollback()
        app.logger.error("Chat deletion error: %s", exc)
        return jsonify({"error": "Failed to delete chat", "success": False}), 500


# --- File uploads ---

@app.route("/api/upload", methods=["POST"])
@login_required_api
@limiter.limit(Config.RATE_LIMIT_UPLOAD)
def upload_file():
    """Save uploaded file and return metadata."""
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file in request", "success": False}), 400

        uploaded_file = request.files["file"]
        if uploaded_file.filename == "":
            return jsonify({"error": "No file selected", "success": False}), 400

        if not allowed_file(uploaded_file.filename, app.config["ALLOWED_EXTENSIONS"]):
            allowed = ", ".join(sorted(app.config["ALLOWED_EXTENSIONS"]))
            return jsonify({"error": f"Format not allowed. Supported: {allowed}", "success": False}), 400

        original_name = secure_filename(uploaded_file.filename)
        if not original_name:
            return jsonify({"error": "Invalid filename", "success": False}), 400

        unique_name = f"{uuid.uuid4().hex}_{original_name}"

        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        uploaded_file.save(save_path)

        # Validate MIME type after save
        if not validate_mime_type(save_path):
            os.remove(save_path)
            return jsonify({"error": "File content does not match expected type", "success": False}), 400

        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
        is_image = ext in {"png", "jpg", "jpeg", "gif"}
        extracted_text = extract_text_from_file(save_path)
        preview = ""
        if extracted_text and not is_image:
            preview = extracted_text[:300] + ("..." if len(extracted_text) > 300 else "")

        app.logger.info("File uploaded: %s -> %s", original_name, unique_name)
        return jsonify({
            "success": True,
            "file_id": unique_name,
            "file_name": original_name,
            "preview": preview,
            "is_image": is_image,
        })

    except Exception as exc:
        app.logger.error("Upload error: %s", exc)
        return jsonify({"error": "Upload failed. Please try again.", "success": False}), 500


# --- Messages / LLM ---

@app.route("/api/chats/<chat_id>/message", methods=["POST"])
@login_required_api
@limiter.limit(Config.RATE_LIMIT_MESSAGE)
def post_message(chat_id):
    try:
        user = get_current_user()
        chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
        if not chat:
            return jsonify({"error": "Chat not found", "success": False}), 404

        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request", "success": False}), 400

        user_prompt = data.get("message", "").strip()
        file_id = data.get("file_id")
        file_name = data.get("file_name")

        if not user_prompt:
            return jsonify({"error": "Message is empty", "success": False}), 400
        if len(user_prompt) > app.config["MAX_PROMPT_CHARS"]:
            return jsonify({"error": f"Message too long (max {app.config['MAX_PROMPT_CHARS']} characters)", "success": False}), 400

        # Resolve uploaded file path
        file_path = None
        if file_id:
            candidate = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(file_id))
            if os.path.exists(candidate):
                file_path = candidate

        # Persist user message
        user_msg = Message(chat_id=chat.id, role="user", content=user_prompt, file_path=file_path, file_name=file_name)
        db.session.add(user_msg)

        # Auto-title on first message
        if chat.messages.count() <= 1:
            chat.title = user_prompt[:50] + ("..." if len(user_prompt) > 50 else "")

        db.session.commit()

        # Build LLM context with SQL LIMIT (not Python slicing)
        system_prompt = (
            "You are a helpful, professional, and friendly AI assistant. "
            "Give well-structured markdown formatted responses. "
            "Use code blocks with language identifiers for code."
        )
        messages_payload = [{"role": "system", "content": system_prompt}]

        max_ctx = app.config["MAX_CONTEXT_MESSAGES"]
        recent = (
            chat.messages
            .order_by(Message.timestamp.desc())
            .limit(max_ctx)
            .all()
        )
        recent.reverse()  # chronological order

        for msg in recent:
            content = msg.content
            if msg.role == "user" and msg.file_path:
                ext = (msg.file_name or "").rsplit(".", 1)[-1].lower()
                if ext in {"png", "jpg", "jpeg", "gif"}:
                    content = f"[Attached Image: {msg.file_name}]\n\nQuery: {msg.content}"
                else:
                    doc_text = extract_text_from_file(msg.file_path)
                    if doc_text:
                        truncated = doc_text[: app.config["MAX_DOC_CHARS"]]
                        content = (
                            f"[Attached Document: {msg.file_name}]\n"
                            f"--- DOCUMENT CONTENT ---\n{truncated}\n--- END DOCUMENT ---\n\n"
                            f"User Question: {msg.content}"
                        )
            messages_payload.append({"role": msg.role, "content": content})

        if not groq_client:
            return jsonify({"error": "AI service not configured", "success": False}), 503

        try:
            ai_text = call_llm_with_retry(messages=messages_payload, model=app.config["GROQ_MODEL"])
        except ValueError as exc:
            # These are our sanitized error messages from call_llm_with_retry
            return jsonify({"error": str(exc), "success": False}), 503

        ai_msg = Message(chat_id=chat.id, role="assistant", content=ai_text)
        db.session.add(ai_msg)
        db.session.commit()

        return jsonify({"success": True, "response": ai_text, "chat": chat.to_dict()})

    except Exception as exc:
        db.session.rollback()
        app.logger.error("Message endpoint error: %s", exc)
        return jsonify({"error": "Failed to process message. Please try again.", "success": False}), 500


# --- Health check ---

@app.route("/health")
def health():
    status = "healthy"
    checks: dict = {}

    try:
        db.session.execute(db.text("SELECT 1"))
        checks["database"] = "connected"
    except Exception:
        status = "degraded"
        checks["database"] = "unavailable"

    checks["ai_configured"] = app.config["GROQ_API_KEY"] is not None
    checks["model"] = app.config["GROQ_MODEL"]
    checks["environment"] = "vercel" if IS_VERCEL else ("production" if IS_PRODUCTION else "development")

    return jsonify({"status": status, "timestamp": datetime.now(timezone.utc).isoformat(), "checks": checks})


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found", "success": False}), 404
    return render_template("index.html"), 404


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({
        "error": "Too many requests. Please slow down.",
        "success": False,
    }), 429


@app.errorhandler(413)
def request_entity_too_large(e):
    return jsonify({
        "error": f"File too large. Maximum size is {app.config['MAX_CONTENT_LENGTH'] // (1024*1024)}MB.",
        "success": False,
    }), 413


@app.errorhandler(500)
def internal_error(e):
    db.session.rollback()
    return jsonify({"error": "An internal error occurred", "success": False}), 500


# ---------------------------------------------------------------------------
# Dev server entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)