from flask import Flask, render_template, request, jsonify, session
from sambanova import SambaNova
import os
from dotenv import load_dotenv
import secrets
import uuid

# Load environment variables
load_dotenv()

# Flask app setup
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# Configure SambaNova API - FIXED
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY", "dccd4b12-74e5-4996-9b22-deecd566404f")
if not SAMBANOVA_API_KEY:
    raise ValueError("SAMBANOVA_API_KEY is not set in environment or .env file")
SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1"
MODEL_NAME = "DeepSeek-V3.1"

# Initialize SambaNova client - CORRECTED
client = SambaNova(
    api_key=SAMBANOVA_API_KEY,
    base_url=SAMBANOVA_BASE_URL,
)

def get_user_id():
    """
    Assigns and returns a unique user id in session if not present.
    """
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    return session['user_id']

def get_session_conversations():
    """
    Gets the conversations dict from session, initializing if needed.
    Structure: {user_id1: [msg1, msg2, ...], user_id2: [...], ...}
    """
    if 'multi_conversations' not in session:
        session['multi_conversations'] = {}
    return session['multi_conversations']

def get_user_conversation():
    """
    Return conversation list of the current session user,
    initializing if missing.
    """
    user_id = get_user_id()
    all_convos = get_session_conversations()
    if user_id not in all_convos:
        all_convos[user_id] = []
        session['multi_conversations'] = all_convos
    return all_convos[user_id]

def set_user_conversation(history):
    """ 
    Sets the conversation list of the current session user.
    """
    user_id = get_user_id()
    all_convos = get_session_conversations()
    all_convos[user_id] = history
    session['multi_conversations'] = all_convos

@app.route('/')
def home():
    """Render the main chat page"""
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    """Handle chat messages in a multi-user/session setup"""
    try:
        data = request.get_json()
        user_message = data.get('message', '')

        print(f"📩 Received message: {user_message}")

        if not user_message.strip():
            return jsonify({'error': 'Empty message', 'success': False}), 400

        # Retrieve conversation history for this user/session
        conversation_history = get_user_conversation()

        messages = [
            {"role": "system", "content": "You are a helpful AI assistant."}
        ]

        # Add previous conversation history
        for idx, message in enumerate(conversation_history):
            if idx % 2 == 0:
                messages.append({"role": "user", "content": message})
            else:
                messages.append({"role": "assistant", "content": message})

        # Add the current user input
        messages.append({"role": "user", "content": user_message})

        print(f"🤖 Calling SambaNova API with {len(messages)} messages...")

        # Generate response using SambaNova API - FIXED
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.1,
            top_p=0.1
        )

        assistant_message = response.choices[0].message.content

        print(f"✅ Got response: {assistant_message[:100]}...")

        # Update current user's conversation history
        conversation_history.append(user_message)
        conversation_history.append(assistant_message)
        set_user_conversation(conversation_history)

        return jsonify({
            'response': assistant_message,
            'success': True
        })

    except Exception as e:
        print(f"❌ Error generating response: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': f'An error occurred: {str(e)}',
            'success': False
        }), 500

@app.route('/clear', methods=['POST'])
def clear_chat():
    """Clear only the current session user's conversation history"""
    user_id = get_user_id()
    all_convos = get_session_conversations()
    all_convos[user_id] = []
    session['multi_conversations'] = all_convos
    print(f"🗑️ Cleared chat for user: {user_id}")
    return jsonify({'success': True, 'message': 'Chat cleared'})

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy', 
        'model': MODEL_NAME,
        'api_key': SAMBANOVA_API_KEY[:10] + '...' if SAMBANOVA_API_KEY else 'Not set'
    })

# REPLACE WITH:
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)