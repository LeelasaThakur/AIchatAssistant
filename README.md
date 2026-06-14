# Secure AI Chat Assistant (Production-Ready)

A modern, responsive, secure, and production-ready Flask-based AI Chat Assistant powered by SambaNova's high-speed API and the DeepSeek model.

This application has been refactored and audited to support multiple users, SQL database persistence (SQLite for development / PostgreSQL ready), secure cookie sessions, CSRF protection, file upload content grounding, and containerized deployment.

---

## Key Features

1. **Secure Database Persistence**: All conversations, users, settings, and messages are persisted in an SQL database using the SQLAlchemy ORM.
2. **Server-Side Authentication**: Replaces vulnerable frontend credential mocks with secure session-based authentication using Bcrypt hashed passwords.
3. **CSRF Protection**: All state-modifying requests (`POST`, `DELETE`) are protected with global CSRF verification middleware (`Flask-WTF`).
4. **API Key Separation**: Zero hardcoded API keys. Configurations are loaded dynamically from environment variables or a local `.env` file.
5. **Context Window Management**: Limits the conversational context passed to the LLM to the last 15 messages. The full history remains accessible in the UI.
6. **File Attachment Grounding**: Upload TXT, PDF, Word (DOCX) documents or images. Text content is parsed and fed directly to the LLM as context for answering user questions.
7. **Premium Responsive Styling**: Features dynamic dark mode persistence, smooth slide-out sidebar interactions, file badge attachments, typing indicators, copy-to-clipboard elements, and mobile scaling.

---

## Project Structure

```
AIchatAssistant/
│
├── instance/               # SQLite local database directory (Flask default)
├── uploads/                # Directory for storing user uploaded files
├── templates/
│   └── index.html          # Refactored front-end template with login/signup portals
├── .env.example            # Environmental configurations template
├── app.py                  # Main Flask application with endpoints & LLM logic
├── config.py               # Central application configuration class
├── document_parser.py      # PDF, Word (DOCX), and Text parsing utility
├── extensions.py           # Shared DB, Bcrypt, and CSRF extension instances
├── models.py               # SQLAlchemy Database schemas (User, Chat, Message)
├── requirements.txt        # Full Python library dependencies
├── Dockerfile              # Docker compilation setup
└── README.md               # Documentation
```

---

## Quick Start (Local Run)

### 1. Prerequisites
Ensure you have **Python 3.10+** installed.

### 2. Setup environment variables
Create a `.env` file in the root folder:
```bash
cp .env.example .env
```
Fill in your `SAMBANOVA_API_KEY` in the newly created `.env` file.

### 3. Install dependencies
Run pip install:
```bash
pip install -r requirements.txt
```

### 4. Launch the application
```bash
python app.py
```
Open [http://localhost:5000](http://localhost:5000) in your browser.

---

## Production Deployment

### running with Docker
Build the docker container:
```bash
docker build -t ai-chat-assistant .
```
Run the container (passing environment variables):
```bash
docker run -d -p 5000:5000 --env-file .env -v $(pwd)/instance:/app/instance -v $(pwd)/uploads:/app/uploads ai-chat-assistant
```

### Production Security Configuration
Ensure you change the following variables in your production environment:
*   `SECRET_KEY`: Set to a secure, random string (e.g. `secrets.token_hex(24)`).
*   `SESSION_COOKIE_SECURE`: Set to `True` to mandate HTTPS cookies.
*   `DATABASE_URL`: Configure a production database URI (e.g., PostgreSQL).
*   `SAMBANOVA_API_KEY`: Never hardcode this key; pass it securely via platform secrets.
