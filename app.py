from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import openai
import os
from dotenv import load_dotenv
import secrets
import uuid

# Load environment variables
load_dotenv()

app = Flask(__name__,template_folder='.')
app.secret_key = secrets.token_hex(16) 
 # Generate a secret key for sessionsgit 

# Configure SambaNova API
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY", "939ebbeb-e6f4-402b-9b37-91e6c43dc926")
SAMBANOVA_BASE_URL = "https://api.sambanova.ai/v1"
MODEL_NAME = "Llama-3.3-Swallow-70B-Instruct-v0.4"

# Initialize OpenAI client with SambaNova endpoint
client = openai.OpenAI(
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
        session['multi_conversations'] = all_convos  # must "write back" in Flask session
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

        if not user_message.strip():
            return jsonify({'error': 'Empty message'}), 400

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

        # Generate response using SambaNova API
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.7,
            top_p=0.9,
            max_tokens=500
        )

        assistant_message = response.choices[0].message.content

        # Update current user's conversation history
        conversation_history.append(user_message)
        conversation_history.append(assistant_message)
        set_user_conversation(conversation_history)

        return jsonify({
            'response': assistant_message,
            'success': True
        })

    except Exception as e:
        print(f"Error generating response: {e}")
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
    return jsonify({'success': True, 'message': 'Chat cleared'})

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'model': MODEL_NAME})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)