import os
import uuid
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename

# Use the official Groq SDK instead of openai shim
from groq import Groq

from config import Config, IS_VERCEL, IS_PRODUCTION
from extensions import db, bcrypt, csrf
from models import User, Chat, Message
from document_parser import allowed_file, extract_text_from_file

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config.from_object(Config)

# Initialise extensions
db.init_app(app)
bcrypt.init_app(app)
csrf.init_app(app)

# ---------------------------------------------------------------------------
# Upload directory
# ---------------------------------------------------------------------------
# On Vercel /tmp is always writable; everywhere else use the configured folder.
# We wrap this in a try/except so a cold-start never hard-crashes due to a
# permission issue on the filesystem.
try:
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
except OSError as exc:
    # Non-fatal: log and continue.  Upload requests will fail gracefully.
    logging.warning("Could not create upload folder %s: %s", app.config["UPLOAD_FOLDER"], exc)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Stream-only logging on Vercel (no writable log directory).
# On local/Docker, also write to a rotating file.
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
# Root cause fix: the openai shim (OpenAI(base_url=groq_url)) injects
# `proxies` into httpx which the current httpx version does not accept.
# The official `groq` SDK handles its own httpx client correctly.
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
# db.create_all() is safe to call repeatedly; on PostgreSQL it only creates
# tables that don't already exist (no destructive migrations).
with app.app_context():
    try:
        db.create_all()
        app.logger.info("Database tables initialised/verified.")
    except Exception as exc:
        # Log but don't crash the process – the health endpoint will surface
        # the problem and requests that need the DB will fail with 500.
        app.logger.error("Error creating database tables: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_current_user() -> User | None:
    """Return the logged-in User from the session, or None."""
    user_id = session.get("user_id")
    if user_id:
        return db.session.get(User, user_id)  # SQLAlchemy 2.x: use Session.get()
    return None


def login_required_api(f):
    """Decorator that enforces authentication for JSON API endpoints."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Authentication required", "success": False}), 401
        return f(*args, **kwargs)

    return decorated


def call_llm_with_retry(messages: list, model: str, max_retries: int = 3, base_delay: float = 1.0) -> str:
    """Call the Groq chat completion API with exponential-backoff retries."""
    if not groq_client:
        raise ValueError("Groq client is not configured. Missing GROQ_API_KEY.")

    import time

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
                time.sleep(base_delay * (2**attempt))

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


# --- Auth ---

@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request JSON", "success": False}), 400

        username = data.get("username", "").strip()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not username or not email or not password:
            return jsonify({"error": "All fields are required", "success": False}), 400
        if len(username) < 3:
            return jsonify({"error": "Username must be at least 3 characters", "success": False}), 400
        if len(password) < 6:
            return jsonify({"error": "Password must be at least 6 characters", "success": False}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({"error": "Username is already taken", "success": False}), 400
        if User.query.filter_by(email=email).first():
            return jsonify({"error": "Email is already registered", "success": False}), 400

        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        session.clear()
        session["user_id"] = new_user.id
        session.permanent = True

        app.logger.info("Registered user: %s", username)
        return jsonify({"success": True, "message": "Account created successfully", "user": new_user.to_dict()})

    except Exception as exc:
        db.session.rollback()
        app.logger.error("Registration error: %s", exc)
        return jsonify({"error": "Registration failed. Please try again.", "success": False}), 500


@app.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request JSON", "success": False}), 400

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({"error": "Username and password are required", "success": False}), 400

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            app.logger.info("Logged in user: %s", username)
            return jsonify({"success": True, "message": "Login successful", "user": user.to_dict()})

        return jsonify({"error": "Invalid username or password", "success": False}), 401

    except Exception as exc:
        app.logger.error("Login error: %s", exc)
        return jsonify({"error": "Login error. Please try again.", "success": False}), 500


@app.route("/logout", methods=["POST"])
def logout():
    user = get_current_user()
    username = user.username if user else "unknown"
    session.clear()
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
            return jsonify({"error": "Invalid request JSON", "success": False}), 400
        if "dark_mode" in data:
            user.dark_mode = bool(data["dark_mode"])
            db.session.commit()
            return jsonify({"success": True, "dark_mode": user.dark_mode})
        return jsonify({"error": "Invalid settings field", "success": False}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc), "success": False}), 500


# --- Chats ---

@app.route("/api/chats", methods=["GET"])
@login_required_api
def get_chats():
    user = get_current_user()
    chats = Chat.query.filter_by(user_id=user.id).order_by(Chat.created_at.desc()).all()
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
        return jsonify({"error": str(exc), "success": False}), 500


@app.route("/api/chats/<chat_id>", methods=["GET"])
@login_required_api
def get_chat_messages(chat_id):
    user = get_current_user()
    chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
    if not chat:
        return jsonify({"error": "Chat not found or access denied", "success": False}), 404
    return jsonify({"success": True, "chat": chat.to_dict(), "messages": [m.to_dict() for m in chat.messages]})


@app.route("/api/chats/<chat_id>", methods=["DELETE"])
@login_required_api
def delete_chat(chat_id):
    try:
        user = get_current_user()
        chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
        if not chat:
            return jsonify({"error": "Chat not found or access denied", "success": False}), 404

        # Best-effort cleanup of /tmp files (they'll vanish anyway on Vercel)
        for message in chat.messages:
            if message.file_path and os.path.exists(message.file_path):
                try:
                    os.remove(message.file_path)
                except OSError as exc:
                    app.logger.warning("Could not delete file %s: %s", message.file_path, exc)

        db.session.delete(chat)
        db.session.commit()
        app.logger.info("Deleted chat %s", chat_id)
        return jsonify({"success": True, "message": "Chat deleted successfully"})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc), "success": False}), 500


# --- File uploads ---

@app.route("/api/upload", methods=["POST"])
@login_required_api
def upload_file():
    """
    Save uploaded file to UPLOAD_FOLDER (/tmp/uploads on Vercel).

    Note: /tmp on Vercel is ephemeral and local to the invocation.  Files
    survive only for the duration of a single serverless function execution.
    For durable storage across sessions, replace this with an S3/R2/GCS
    upload and store the object URL in the database instead of file_path.
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file in request", "success": False}), 400

        uploaded_file = request.files["file"]
        if uploaded_file.filename == "":
            return jsonify({"error": "No file selected", "success": False}), 400

        if not allowed_file(uploaded_file.filename, app.config["ALLOWED_EXTENSIONS"]):
            return jsonify(
                {"error": f"Format not allowed. Supported: {', '.join(app.config['ALLOWED_EXTENSIONS'])}", "success": False}
            ), 400

        original_name = secure_filename(uploaded_file.filename)
        unique_name = f"{uuid.uuid4().hex}_{original_name}"

        # Ensure /tmp/uploads exists (may not persist between cold starts)
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        uploaded_file.save(save_path)

        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
        is_image = ext in {"png", "jpg", "jpeg", "gif"}
        extracted_text = extract_text_from_file(save_path)
        preview = ""
        if extracted_text and not is_image:
            preview = extracted_text[:300] + ("..." if len(extracted_text) > 300 else "")

        app.logger.info("File uploaded: %s -> %s", original_name, unique_name)
        return jsonify(
            {"success": True, "file_id": unique_name, "file_name": original_name, "preview": preview, "is_image": is_image}
        )

    except Exception as exc:
        app.logger.error("Upload error: %s", exc)
        return jsonify({"error": f"Upload failed: {exc}", "success": False}), 500


# --- Messages / LLM ---

@app.route("/api/chats/<chat_id>/message", methods=["POST"])
@login_required_api
def post_message(chat_id):
    try:
        user = get_current_user()
        chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
        if not chat:
            return jsonify({"error": "Chat not found or access denied", "success": False}), 404

        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid request JSON", "success": False}), 400

        user_prompt = data.get("message", "").strip()
        file_id = data.get("file_id")
        file_name = data.get("file_name")

        if not user_prompt:
            return jsonify({"error": "Message is empty", "success": False}), 400
        if len(user_prompt) > app.config["MAX_PROMPT_CHARS"]:
            return jsonify({"error": "Message too long", "success": False}), 400

        # Resolve uploaded file path (only valid within same /tmp lifecycle)
        file_path = None
        if file_id:
            candidate = os.path.join(app.config["UPLOAD_FOLDER"], secure_filename(file_id))
            if os.path.exists(candidate):
                file_path = candidate

        # Persist user message
        user_msg = Message(chat_id=chat.id, role="user", content=user_prompt, file_path=file_path, file_name=file_name)
        db.session.add(user_msg)

        if len(chat.messages) == 0:
            chat.title = user_prompt[:30] + ("..." if len(user_prompt) > 30 else "")

        db.session.commit()

        # Build LLM context (last 15 messages)
        messages_payload = [
            {"role": "system", "content": "You are a helpful, professional, and friendly AI assistant. Give markdown formatted responses."}
        ]
        recent = Message.query.filter_by(chat_id=chat.id).order_by(Message.timestamp.asc()).all()
        if len(recent) > 15:
            recent = recent[-15:]

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
                            f"--- EXTRACTED CONTENT ---\n{truncated}\n--- END ---\n\n"
                            f"Query: {msg.content}"
                        )
            messages_payload.append({"role": msg.role, "content": content})

        if not groq_client:
            return jsonify({"error": "Groq client not configured. Set GROQ_API_KEY.", "success": False}), 500

        try:
            ai_text = call_llm_with_retry(messages=messages_payload, model=app.config["GROQ_MODEL"])
        except Exception as exc:
            app.logger.error("Groq completion error: %s", exc)
            return jsonify({"error": f"AI response failed: {exc}", "success": False}), 500

        ai_msg = Message(chat_id=chat.id, role="assistant", content=ai_text)
        db.session.add(ai_msg)
        db.session.commit()

        return jsonify({"success": True, "response": ai_text, "chat": chat.to_dict()})

    except Exception as exc:
        db.session.rollback()
        app.logger.error("Message endpoint error: %s", exc)
        return jsonify({"error": str(exc), "success": False}), 500


# --- Health check ---

@app.route("/health")
def health():
    status = "healthy"
    checks: dict = {}

    try:
        db.session.execute(db.text("SELECT 1"))
        checks["database"] = "connected"
    except Exception as exc:
        status = "degraded"
        checks["database"] = f"failed: {exc}"

    checks["groq_configured"] = app.config["GROQ_API_KEY"] is not None
    checks["model"] = app.config["GROQ_MODEL"]
    checks["environment"] = "vercel" if IS_VERCEL else ("production" if IS_PRODUCTION else "development")

    return jsonify({"status": status, "timestamp": datetime.utcnow().isoformat(), "checks": checks})


# ---------------------------------------------------------------------------
# Dev server entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)