# Deployment Guide & Root Cause Analysis

## Root Cause Analysis

### Bug 1 – `Client.__init__() got an unexpected keyword argument 'proxies'`

**File:** `app.py` (lines 53–56)  
**Cause:** The code used `openai.OpenAI(base_url=GROQ_URL)` as a shim to reach the Groq API.  
The current `openai` SDK (≥1.x) creates an `httpx.Client` internally and passes a `proxies` kwarg to it.  
Modern `httpx` (≥0.28) removed the `proxies` parameter; using `proxies` now raises a `TypeError`.  
This conflict is triggered by `openai` being pinned at `1.30.1` while Vercel's environment pulls in a newer `httpx`.

**Fix:** Replace `openai` with the official `groq` SDK (`groq==0.9.0`).  
The `groq` package ships its own httpx client, correctly avoiding the `proxies` kwarg.  

```python
# Before (broken)
from openai import OpenAI
client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")

# After (correct)
from groq import Groq
client = Groq(api_key=key)
```

---

### Bug 2 – `sqlite3.OperationalError: unable to open database file`

**File:** `config.py`  
**Cause:** SQLite stores its file at `instance/chat_assistant.db` relative to the project root.  
On Vercel, the project is deployed to `/var/task/` which is a **read-only** filesystem.  
SQLite cannot create or write a `.db` file there, so every DB operation raises `OperationalError`.

**Fix:**  
- Detect the Vercel environment (`IS_VERCEL` flag via the `VERCEL` env var).  
- In production, require a `DATABASE_URL` pointing to a real PostgreSQL database.  
- In local development, continue using SQLite as a fallback.  
- Added `pool_pre_ping=True` and `pool_recycle=300` for serverless connection hygiene.  

---

### Bug 3 – `OSError: [Errno 30] Read-only file system: '/var/task/uploads'`

**Files:** `app.py`, `config.py`  
**Cause:** On startup, `app.py` called `os.makedirs(app.config['UPLOAD_FOLDER'])` where `UPLOAD_FOLDER` resolved to a path inside `/var/task/` (the read-only Vercel bundle).

**Fix:**  
- `config.py` now sets `UPLOAD_FOLDER = "/tmp/uploads"` whenever `IS_VERCEL` is true.  
- `app.py` wraps the `os.makedirs` call in a `try/except OSError` so a filesystem permission error never crashes the process.  
- The `/api/upload` handler also calls `os.makedirs(..., exist_ok=True)` before each save, because `/tmp` directories do not persist between cold starts.

**Important caveat:** `/tmp` on Vercel is local to a single function invocation and is not shared across concurrent invocations or cold starts. Uploaded files are therefore ephemeral. For durable file storage, replace the local save with an upload to S3/R2/GCS and store the object URL in the `Message.file_path` column.

---

### Bug 4 – `logs/` directory creation on read-only filesystem

**File:** `app.py` (line 33)  
**Cause:** `os.makedirs('logs', exist_ok=True)` ran unconditionally during module import on Vercel.

**Fix:** The log-directory creation is now gated on `not IS_VERCEL and not app.debug`, and wrapped in a `try/except OSError`.

---

### Bug 5 – `User.query.get(user_id)` deprecation warning (SQLAlchemy 2.x)

**File:** `app.py` (`get_current_user`)  
**Cause:** `Query.get()` was removed in SQLAlchemy 2.0.

**Fix:** Replaced with `db.session.get(User, user_id)`.

---

## Files Changed

| File | Change summary |
|------|---------------|
| `requirements.txt` | Removed `openai`; added `groq==0.9.0`, `psycopg2-binary==2.9.9`, pinned `SQLAlchemy==2.0.31` |
| `config.py` | Environment detection; PostgreSQL URI builder; serverless pool options; `/tmp` upload path |
| `app.py` | Groq SDK; safe `os.makedirs`; SQLAlchemy 2.x `session.get()`; all original routes preserved |
| `models.py` | Minor type annotations; no schema changes |
| `document_parser.py` | Minor style cleanup; no functional changes |
| `extensions.py` | No changes |
| `vercel.json` | Added `maxDuration: 30` for LLM latency headroom |
| `Dockerfile` | Added `libpq-dev`; added `--timeout 60` to gunicorn |
| `.env.example` | New file documenting all required variables |

---

## Environment Variables

### Required on Vercel

| Variable | Description | Example |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session signing key | `secrets.token_hex(32)` output |
| `GROQ_API_KEY` | Groq API key | `gsk_...` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg2://user:pass@host/db` |
| `SESSION_COOKIE_SECURE` | Set `true` in HTTPS production | `true` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Override the Groq model |
| `FLASK_ENV` | auto-detected | `production` to force production mode locally |

---

## Step-by-Step Deployment

### 1. Set up a PostgreSQL database

Use any of: **Vercel Postgres** (Neon), **Supabase**, **Railway**, **Render**, or **Aiven**.

**Vercel Postgres (recommended – zero-config):**
```bash
vercel integration add neon   # or use dashboard Storage tab
```
This automatically injects `DATABASE_URL`, `POSTGRES_URL`, etc.

**Manual (any provider):**
```bash
# Get your connection string, then:
vercel env add DATABASE_URL
# Paste: postgresql+psycopg2://user:pass@host:5432/dbname
```

### 2. Add remaining environment variables

```bash
vercel env add SECRET_KEY
vercel env add GROQ_API_KEY
vercel env add SESSION_COOKIE_SECURE   # value: true
```

### 3. Deploy

```bash
# Install Vercel CLI if needed
npm i -g vercel

# From project root
vercel --prod
```

### 4. Verify

```bash
curl https://your-app.vercel.app/health
# Expected: {"status":"healthy","checks":{"database":"connected",...}}
```

---

## Local Development

```bash
# 1. Clone and install
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env – at minimum set SECRET_KEY and GROQ_API_KEY
# DATABASE_URL can be left blank; SQLite will be used automatically

# 3. Run
python app.py
# → http://localhost:5000
```

---

## Docker (non-Vercel production)

```bash
docker build -t ai-chat-assistant .

docker run -d \
  -p 5000:5000 \
  --env-file .env \
  -v $(pwd)/instance:/app/instance \
  -v $(pwd)/uploads:/app/uploads \
  ai-chat-assistant
```

Set `DATABASE_URL` to a real PostgreSQL URI in `.env` for production Docker deployments.

---

## Additional Issues Discovered

1. **File attachments are ephemeral on Vercel.** `/tmp` is not shared across function replicas or cold starts. A file uploaded in one invocation will not exist in a subsequent invocation that reads `message.file_path`. **Recommendation:** integrate an object-storage service (AWS S3, Cloudflare R2, Supabase Storage) and store signed URLs in the database.

2. **`db.create_all()` is not a migration tool.** It creates missing tables but never alters existing ones. For schema changes in production, use **Flask-Migrate** (Alembic). Add it when the schema stabilises.

3. **Sessions are in-memory cookies.** The `SECRET_KEY` must be stable across Vercel deployments; never leave it as `os.urandom(24).hex()` in production (a new key is generated on every cold start, invalidating all sessions). Always set `SECRET_KEY` as a fixed environment variable.

4. **CSRF tokens and Vercel.** Vercel can route a single request to any of several function replicas. Because Flask-WTF stores CSRF state in the session cookie (client-side), this is fine—no server-side state is required.

5. **`gunicorn --workers 4` on Vercel is irrelevant.** Vercel runs each request in an isolated function container; gunicorn's worker count has no effect. The `CMD` in `Dockerfile` is only used for Docker deployments.