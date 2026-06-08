import os
import uuid
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.utils import secure_filename
from openai import OpenAI

# Import configs, extensions and models
from config import Config
from extensions import db, bcrypt, csrf
from models import User, Chat, Message
from document_parser import allowed_file, extract_text_from_file

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Initialize Extensions
db.init_app(app)
bcrypt.init_app(app)
csrf.init_app(app)

# Ensure folders exist
if os.environ.get("VERCEL"):
    os.makedirs("/tmp/uploads", exist_ok=True)
else:
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
# Configure Production Logging

if not app.debug and not os.environ.get("VERCEL"):
    os.makedirs('logs', exist_ok=True)

    file_handler = RotatingFileHandler(
        'logs/chat_assistant.log',
        maxBytes=5 * 1024 * 1024,
        backupCount=5
    )

    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))

    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)

# Initialize Groq client safely
client = None

if app.config['GROQ_API_KEY']:
    try:
        client = OpenAI(
            api_key=app.config['GROQ_API_KEY'],
            base_url=app.config['GROQ_BASE_URL']
        )

        app.logger.info("Groq client initialized successfully.")

    except Exception as e:
        app.logger.error(f"Failed to initialize Groq client: {e}")

else:
    app.logger.warning("GROQ_API_KEY is not set. Chat requests will fail.")# Create database tables
with app.app_context():
    try:
        db.create_all()
        app.logger.info("Database tables initialized/verified.")
    except Exception as e:
        app.logger.error(f"Error creating database tables: {e}")

# Helper: Get current user from session
def get_current_user():
    """Retrieve logged-in User object using session user_id"""
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    return None

# Helper: Require login decorator for APIs
def login_required_api(f):
    """Decorator to enforce auth for JSON endpoints"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'error': 'Authentication required', 'success': False}), 401
        return f(*args, **kwargs)
    return decorated_function

# Call LLM completions with retries & exponential backoff
def call_llm_with_retry(messages, model, max_retries=3, base_delay=1.0):
    """Executes completions requests with retry strategy"""
    if not client:
        raise ValueError("groq client is not configured. Missing API key.")
        
    import time
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            app.logger.info(f"Attempting groq API call (attempt {attempt + 1}/{max_retries})")
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.3,
                top_p=0.9
            )
            return response.choices[0].message.content
        except Exception as e:
            last_exception = e
            app.logger.warning(f"groq completion attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                # Exponential backoff: delay = base_delay * (2 ^ attempt)
                time.sleep(base_delay * (2 ** attempt))
                
    raise last_exception

# =====================================================================
# ROUTES
# =====================================================================

@app.route('/')
def home():
    """Render the chat/login home page"""
    return render_template('index.html')

# --- AUTH ROUTES ---

@app.route('/register', methods=['POST'])
def register():
    """Register a new user account"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request JSON', 'success': False}), 400
            
        username = data.get('username', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        # Basic validations
        if not username or not email or not password:
            return jsonify({'error': 'All fields are required', 'success': False}), 400
            
        if len(username) < 3:
            return jsonify({'error': 'Username must be at least 3 characters', 'success': False}), 400
            
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters', 'success': False}), 400

        # Check existing user
        if User.query.filter_by(username=username).first():
            return jsonify({'error': 'Username is already taken', 'success': False}), 400
            
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Email is already registered', 'success': False}), 400

        # Create new user
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        
        db.session.add(new_user)
        db.session.commit()

        # Login session auto-start
        session.clear()
        session['user_id'] = new_user.id
        session.permanent = True

        app.logger.info(f"Registered user: {username}")
        return jsonify({
            'success': True,
            'message': 'Account created successfully',
            'user': new_user.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error during registration: {e}")
        return jsonify({'error': 'Registration failed. Please try again.', 'success': False}), 500

@app.route('/login', methods=['POST'])
def login():
    """Authenticate and log in an existing user"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request JSON', 'success': False}), 400
            
        username = data.get('username', '').strip()
        password = data.get('password', '')

        if not username or not password:
            return jsonify({'error': 'Username and password are required', 'success': False}), 400

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session.clear()
            session['user_id'] = user.id
            session.permanent = True
            
            app.logger.info(f"Logged in user: {username}")
            return jsonify({
                'success': True,
                'message': 'Login successful',
                'user': user.to_dict()
            })
            
        return jsonify({'error': 'Invalid username or password', 'success': False}), 401
        
    except Exception as e:
        app.logger.error(f"Error during login: {e}")
        return jsonify({'error': 'Login error. Please try again.', 'success': False}), 500

@app.route('/logout', methods=['POST'])
def logout():
    """Clear session data and log out"""
    username = None
    user = get_current_user()
    if user:
        username = user.username
        
    session.clear()
    app.logger.info(f"Logged out user: {username}")
    return jsonify({'success': True, 'message': 'Logged out successfully'})

@app.route('/api/me', methods=['GET'])
def get_me():
    """Check authentication state and return active user profile"""
    user = get_current_user()
    if user:
        return jsonify({
            'logged_in': True,
            'user': user.to_dict()
        })
    return jsonify({
        'logged_in': False
    })

@app.route('/api/settings', methods=['POST'])
@login_required_api
def update_settings():
    """Update settings (e.g. dark mode state) in database"""
    try:
        user = get_current_user()
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'Invalid request JSON', 'success': False}), 400
        
        if 'dark_mode' in data:
            user.dark_mode = bool(data['dark_mode'])
            db.session.commit()
            return jsonify({'success': True, 'dark_mode': user.dark_mode})
            
        return jsonify({'error': 'Invalid settings field', 'success': False}), 400
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e), 'success': False}), 500

# --- CHAT & CONVERSATION ROUTES ---

@app.route('/api/chats', methods=['GET'])
@login_required_api
def get_chats():
    """Fetch all conversations owned by the user"""
    user = get_current_user()
    chats = Chat.query.filter_by(user_id=user.id).order_by(Chat.created_at.desc()).all()
    return jsonify({
        'success': True,
        'chats': [c.to_dict() for c in chats]
    })

@app.route('/api/chats', methods=['POST'])
@login_required_api
def create_chat():
    """Create a new chat instance"""
    try:
        user = get_current_user()
        chat_id = str(uuid.uuid4())
        
        new_chat = Chat(id=chat_id, user_id=user.id, title="New Chat")
        db.session.add(new_chat)
        db.session.commit()
        
        app.logger.info(f"Created chat {chat_id} for user {user.username}")
        return jsonify({
            'success': True,
            'chat': new_chat.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/api/chats/<chat_id>', methods=['GET'])
@login_required_api
def get_chat_messages(chat_id):
    """Retrieve full history of messages for a chat"""
    user = get_current_user()
    chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
    
    if not chat:
        return jsonify({'error': 'Chat not found or access denied', 'success': False}), 404
        
    messages = [m.to_dict() for m in chat.messages]
    return jsonify({
        'success': True,
        'chat': chat.to_dict(),
        'messages': messages
    })

@app.route('/api/chats/<chat_id>', methods=['DELETE'])
@login_required_api
def delete_chat(chat_id):
    """Permanently delete a chat and associated messages"""
    try:
        user = get_current_user()
        chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
        
        if not chat:
            return jsonify({'error': 'Chat not found or access denied', 'success': False}), 404
            
        # Clean up files associated with this chat
        for message in chat.messages:
            if message.file_path and os.path.exists(message.file_path):
                try:
                    os.remove(message.file_path)
                except Exception as ex:
                    app.logger.error(f"Failed to delete attached file: {message.file_path}. Error: {ex}")
                    
        db.session.delete(chat)
        db.session.commit()
        
        app.logger.info(f"Deleted chat {chat_id} and its associated files.")
        return jsonify({'success': True, 'message': 'Chat deleted successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e), 'success': False}), 500

# --- FILE UPLOADS ---

@app.route('/api/upload', methods=['POST'])
@login_required_api
def upload_file():
    """Handle document uploads, return secure filepath details"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file element in request', 'success': False}), 400
            
        uploaded_file = request.files['file']
        if uploaded_file.filename == '':
            return jsonify({'error': 'No file selected', 'success': False}), 400
            
        if not allowed_file(uploaded_file.filename, app.config['ALLOWED_EXTENSIONS']):
            return jsonify({
                'error': f"File format not allowed. Supported formats: {', '.join(app.config['ALLOWED_EXTENSIONS'])}", 
                'success': False
            }), 400
            
        # Secure filename and make it unique
        original_name = secure_filename(uploaded_file.filename)
        unique_name = f"{uuid.uuid4().hex}_{original_name}"
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        
        uploaded_file.save(save_path)
        
        # Verify if text is extractable and extract a short preview
        extracted_text = extract_text_from_file(save_path)
        preview = ""
        is_image = original_name.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}
        
        if extracted_text and not is_image:
            preview = extracted_text[:300] + ("..." if len(extracted_text) > 300 else "")
            
        app.logger.info(f"File uploaded successfully: {original_name} saved as {unique_name}")
        return jsonify({
            'success': True,
            'file_id': unique_name,
            'file_name': original_name,
            'preview': preview,
            'is_image': is_image
        })
        
    except Exception as e:
        app.logger.error(f"Error during file upload: {e}")
        return jsonify({'error': f"Upload failed: {str(e)}", 'success': False}), 500

# --- CHAT SUBMISSION ---

@app.route('/api/chats/<chat_id>/message', methods=['POST'])
@login_required_api
def post_message(chat_id):
    """Receive user prompt, save messages, query LLM, persist responses"""
    try:
        user = get_current_user()
        chat = Chat.query.filter_by(id=chat_id, user_id=user.id).first()
        
        if not chat:
            return jsonify({'error': 'Chat not found or access denied', 'success': False}), 404
            
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid request JSON', 'success': False}), 400
            
        user_prompt = data.get('message', '').strip()
        file_id = data.get('file_id')
        file_name = data.get('file_name')
        
        if not user_prompt:
            return jsonify({'error': 'Message content is empty', 'success': False}), 400
            
        if len(user_prompt) > app.config['MAX_CONTENT_LENGTH']:
            return jsonify({'error': 'Message length too long', 'success': False}), 400

        # Construct file paths if attachment exists
        file_path = None
        if file_id:
            file_id_secured = secure_filename(file_id)
            possible_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id_secured)
            if os.path.exists(possible_path):
                file_path = possible_path
                
        # Save User Message to Database
        user_message_db = Message(
            chat_id=chat.id,
            role='user',
            content=user_prompt,
            file_path=file_path,
            file_name=file_name
        )
        db.session.add(user_message_db)
        
        # Auto update chat title if it is the first user message in this chat
        if len(chat.messages) == 0:
            chat.title = user_prompt[:30] + ('...' if len(user_prompt) > 30 else '')
            
        db.session.commit()

        # Build message history context for LLM completion
        # We enforce context truncation to maximum 15 messages (plus system instruction)
        # to ensure speed and manage token context window constraints
        messages_payload = [
            {"role": "system", "content": "You are a helpful, professional, and friendly AI assistant. Give markdown formatted responses."}
        ]
        
        recent_messages = Message.query.filter_by(chat_id=chat.id).order_by(Message.timestamp.asc()).all()
        # Truncate context to last 15 messages
        if len(recent_messages) > 15:
            recent_messages = recent_messages[-15:]
            
        for msg in recent_messages:
            payload_content = msg.content
            
            # If the user message has an attachment, extract text and build contextual prompt
            if msg.role == 'user' and msg.file_path:
                is_img = msg.file_name.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}
                if not is_img:
                    doc_content = extract_text_from_file(msg.file_path)
                    if doc_content:
                        payload_content = (
                            f"[Attached Document: {msg.file_name}]\n"
                            f"--- EXTRACTED CONTENT ---\n"
                            f"{doc_content}\n"
                            f"--- END EXTRACTED CONTENT ---\n\n"
                            f"Query: {msg.content}"
                        )
                else:
                    payload_content = (
                        f"[Attached Image: {msg.file_name}] (Note: The user uploaded an image. "
                        f"Acknowledge the image attachment if relevant.)\n\n"
                        f"Query: {msg.content}"
                    )
            
            messages_payload.append({
                "role": msg.role,
                "content": payload_content
            })

        # Check LLM configuration
        if not client:
            return jsonify({
                'error': 'groq client is not configured. Please set GROQ_API_KEY.',
                'success': False
            }), 500

        # Execute completions with retry strategy
        try:
            assistant_response = call_llm_with_retry(
                messages=messages_payload,
                model=app.config['GROQ_MODEL']
            )
        except Exception as api_err:
            app.logger.error(f"groq completions error: {api_err}")
            return jsonify({
                'error': f"Failed to get AI response: {str(api_err)}",
                'success': False
            }), 500

        # Save AI Message to Database
        ai_message_db = Message(
            chat_id=chat.id,
            role='assistant',
            content=assistant_response
        )
        db.session.add(ai_message_db)
        db.session.commit()

        return jsonify({
            'success': True,
            'response': assistant_response,
            'chat': chat.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error handling /message endpoint: {e}")
        return jsonify({'error': str(e), 'success': False}), 500

# --- HEALTH CHECK & METRICS ---

@app.route('/health')
def health():
    """Health check endpoint evaluating DB and API configs"""
    status = 'healthy'
    checks = {}
    
    # 1. Check SQLite DB
    try:
        db.session.execute(db.text('SELECT 1'))
        checks['database'] = 'connected'
    except Exception as e:
        status = 'degraded'
        checks['database'] = f"failed: {str(e)}"
        
    # 2. Check groq API key
    checks['groq_configured'] = app.config['GROQ_API_KEY'] is not None
    checks['model_name'] = app.config['GROQ_MODEL']
    
    return jsonify({
        'status': status,
        'timestamp': datetime.utcnow().isoformat(),
        'checks': checks
    })

if __name__ == '__main__':
    # Run development server
    app.run(debug=True, host='0.0.0.0', port=5000)