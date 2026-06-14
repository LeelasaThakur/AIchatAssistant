from datetime import datetime, timezone
from extensions import db, bcrypt


class User(db.Model):
    """User accounts table."""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    dark_mode = db.Column(db.Boolean, default=False)

    chats = db.relationship("Chat", backref="user", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "dark_mode": self.dark_mode,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Chat(db.Model):
    """Chat conversations table."""
    __tablename__ = "chats"

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(150), nullable=False, default="New Chat")
    pinned = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    messages = db.relationship(
        "Message",
        backref="chat",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="Message.timestamp",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "pinned": self.pinned,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
            "messagesCount": self.messages.count(),
        }


class Message(db.Model):
    """Individual chat messages table."""
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(
        db.String(36),
        db.ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = db.Column(db.String(20), nullable=False)   # "user" | "assistant"
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # Ephemeral file references – valid only within same /tmp lifecycle on Vercel.
    file_path = db.Column(db.String(300), nullable=True)
    file_name = db.Column(db.String(150), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chatId": self.chat_id,
            "role": self.role,
            "content": self.content,
            "isUser": self.role == "user",
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "fileName": self.file_name,
            "hasAttachment": self.file_path is not None,
        }