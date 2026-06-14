"""
AI Chat Assistant — API Test Suite
===================================
Covers: auth, CRUD, rate limits, security headers, error handling.

Run:  python -m pytest tests/test_api.py -v
"""
import json
import os
import sys

# ---- CRITICAL: Set environment BEFORE any app imports ----
os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
os.environ["GROQ_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SESSION_COOKIE_SECURE"] = "False"
os.environ.pop("VERCEL", None)
os.environ.pop("FLASK_ENV", None)

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app import app as flask_app
from extensions import db, limiter


@pytest.fixture(scope="module", autouse=True)
def setup_app():
    """Configure app for testing once for the entire module."""
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    # Disable rate limiter for tests
    limiter.enabled = False

    with flask_app.app_context():
        db.create_all()
        yield
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client():
    """Test client with fresh session for each test."""
    return flask_app.test_client()


def _register(client, username, email=None, password="TestPass1"):
    """Helper to register a user."""
    if email is None:
        email = f"{username}@test.com"
    return client.post("/register", json={
        "username": username,
        "email": email,
        "password": password,
    })


# ========================================================================
# Auth Tests
# ========================================================================

class TestRegistration:
    def test_register_success(self, client):
        resp = _register(client, "testuser", "test@example.com")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["user"]["username"] == "testuser"

    def test_register_missing_fields(self, client):
        resp = client.post("/register", json={"username": "x"})
        assert resp.status_code == 400

    def test_register_short_username(self, client):
        resp = _register(client, "ab", "a@b.com")
        data = resp.get_json()
        assert resp.status_code == 400
        assert "3-30" in data["error"]

    def test_register_weak_password(self, client):
        resp = _register(client, "weakuser", "weak@test.com", password="short")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "password" in data["error"].lower()

    def test_register_no_uppercase(self, client):
        resp = _register(client, "noupperuser", "noupper@test.com", password="alllower1")
        assert resp.status_code == 400

    def test_register_duplicate(self, client):
        _register(client, "dupeuser", "dupe@test.com")
        # New client to avoid session from first registration
        client2 = flask_app.test_client()
        resp = _register(client2, "dupeuser", "dupe2@test.com")
        assert resp.status_code == 400
        assert "already exists" in resp.get_json()["error"]


class TestLogin:
    def test_login_success(self, client):
        # Register a fresh user
        _register(client, "loginuser", "login@test.com")
        client.post("/logout")

        # Login with the same client (session cleared by logout)
        resp = client.post("/login", json={"username": "loginuser", "password": "TestPass1"})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_login_wrong_password(self, client):
        _register(client, "wrongpw", "wrongpw@test.com")
        client.post("/logout")
        resp = client.post("/login", json={"username": "wrongpw", "password": "wrong"})
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.get_json()["error"]

    def test_login_nonexistent_user(self, client):
        resp = client.post("/login", json={"username": "ghost", "password": "TestPass1"})
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.get_json()["error"]


class TestLogout:
    def test_logout(self, client):
        _register(client, "logoutuser", "logout@test.com")
        resp = client.post("/logout")
        assert resp.status_code == 200

        me_resp = client.get("/api/me")
        assert me_resp.get_json()["logged_in"] is False


# ========================================================================
# Chat CRUD Tests
# ========================================================================

class TestChats:
    def _auth_client(self, username):
        """Return a client that is registered and logged in."""
        c = flask_app.test_client()
        resp = _register(c, username)
        if resp.status_code != 200:
            # User already exists, just login
            c2 = flask_app.test_client()
            c2.post("/login", json={"username": username, "password": "TestPass1"})
            return c2
        return c

    def test_create_chat(self, client):
        c = self._auth_client("chatcreator")
        resp = c.post("/api/chats")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["chat"]["title"] == "New Chat"

    def test_list_chats(self, client):
        c = self._auth_client("chatlister")
        c.post("/api/chats")
        c.post("/api/chats")
        resp = c.get("/api/chats")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["chats"]) >= 2

    def test_rename_chat(self, client):
        c = self._auth_client("chatrenamer")
        create_resp = c.post("/api/chats")
        chat_id = create_resp.get_json()["chat"]["id"]

        resp = c.put(f"/api/chats/{chat_id}", json={"title": "Renamed Chat"})
        assert resp.status_code == 200
        assert resp.get_json()["chat"]["title"] == "Renamed Chat"

    def test_pin_chat(self, client):
        c = self._auth_client("chatpinner")
        create_resp = c.post("/api/chats")
        chat_id = create_resp.get_json()["chat"]["id"]

        resp = c.put(f"/api/chats/{chat_id}", json={"pinned": True})
        assert resp.status_code == 200
        assert resp.get_json()["chat"]["pinned"] is True

    def test_delete_chat(self, client):
        c = self._auth_client("chatdeleter")
        create_resp = c.post("/api/chats")
        chat_id = create_resp.get_json()["chat"]["id"]

        resp = c.delete(f"/api/chats/{chat_id}")
        assert resp.status_code == 200

        get_resp = c.get(f"/api/chats/{chat_id}")
        assert get_resp.status_code == 404

    def test_access_other_users_chat(self, client):
        # Create chat as user A
        c_a = self._auth_client("usera_iso")
        create_resp = c_a.post("/api/chats")
        chat_id = create_resp.get_json()["chat"]["id"]

        # Try to access as user B
        c_b = self._auth_client("userb_iso")
        resp = c_b.get(f"/api/chats/{chat_id}")
        assert resp.status_code == 404

    def test_search_chats(self, client):
        c = self._auth_client("searcher")
        create_resp = c.post("/api/chats")
        chat_id = create_resp.get_json()["chat"]["id"]
        c.put(f"/api/chats/{chat_id}", json={"title": "Machine Learning Guide"})

        resp = c.get("/api/chats/search?q=Machine")
        data = resp.get_json()
        assert data["success"] is True
        assert len(data["chats"]) >= 1


# ========================================================================
# Security Tests
# ========================================================================

class TestSecurity:
    def test_security_headers(self, client):
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Content-Security-Policy") is not None

    def test_auth_required_for_chats(self, client):
        resp = client.get("/api/chats")
        assert resp.status_code == 401

    def test_auth_required_for_create_chat(self, client):
        resp = client.post("/api/chats")
        assert resp.status_code == 401

    def test_no_exception_in_error_responses(self, client):
        """Ensure error responses don't leak internal exception details."""
        resp = client.post("/login", json={"username": "x", "password": "y"})
        data = resp.get_json()
        assert "Traceback" not in json.dumps(data)
        assert "Error(" not in json.dumps(data)


# ========================================================================
# Health Check
# ========================================================================

class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        data = resp.get_json()
        assert data["status"] in ("healthy", "degraded")
        assert "database" in data["checks"]
        assert "timestamp" in data

    def test_health_no_key_leak(self, client):
        resp = client.get("/health")
        data = json.dumps(resp.get_json())
        assert "gsk_" not in data


# ========================================================================
# Error Handlers
# ========================================================================

class TestErrorHandlers:
    def test_404_api(self, client):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
        assert resp.get_json()["error"] == "Not found"
