from datetime import datetime
from extensions import db, bcrypt

class User(db.Model):
    """User accounts table model"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    dark_mode = db.Column(db.Boolean, default=False)
    
    # Relationships
    chats = db.relationship('Chat', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        """Hashes the password and sets password_hash"""
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        """Checks if the password matches the stored hash"""
        return bcrypt.check_password_hash(self.password_hash, password)

    def to_dict(self):
        """Serialize user object"""
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'dark_mode': self.dark_mode,
            'created_at': self.created_at.isoformat()
        }


class Chat(db.Model):
    """Chat conversations table model"""
    __tablename__ = 'chats'
    
    id = db.Column(db.String(36), primary_key=True)  # Store UUID string
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(150), nullable=False, default='New Chat')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    messages = db.relationship(
        'Message', 
        backref='chat', 
        lazy=True, 
        cascade='all, delete-orphan',
        order_by='Message.timestamp'
    )

    def to_dict(self):
        """Serialize chat metadata"""
        return {
            'id': self.id,
            'title': self.title,
            'createdAt': self.created_at.isoformat(),
            'messagesCount': len(self.messages)
        }


class Message(db.Model):
    """Conversation messages table model"""
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(36), db.ForeignKey('chats.id', ondelete='CASCADE'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # File attachments metadata
    file_path = db.Column(db.String(300), nullable=True)  # Location of the stored file
    file_name = db.Column(db.String(150), nullable=True)  # Original name of file uploaded

    def to_dict(self):
        """Serialize message details"""
        return {
            'id': self.id,
            'chatId': self.chat_id,
            'role': self.role,
            'content': self.content,
            'isUser': self.role == 'user',
            'timestamp': self.timestamp.isoformat(),
            'fileName': self.file_name,
            'hasAttachment': self.file_path is not None
        }
