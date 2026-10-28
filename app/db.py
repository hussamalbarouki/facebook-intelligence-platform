import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Boolean, DateTime, JSON, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
engine = create_engine(os.getenv('DATABASE_URL', 'sqlite:////tmp/facebook-intelligence-test.db'), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def now():
    return datetime.now(timezone.utc)

class Account(Base):
    __tablename__ = 'accounts'
    id = Column(String(36), primary_key=True)
    label = Column(String(120), nullable=False)
    c_user = Column(String(50))
    token_enc = Column(Text)
    storage_enc = Column(Text)
    is_primary = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    status = Column(String(40), default='unknown')
    last_error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=now)

class Key(Base):
    __tablename__ = 'api_keys'
    id = Column(String(36), primary_key=True)
    label = Column(String(120), nullable=False)
    prefix = Column(String(20), nullable=False)
    secret_hash = Column(String(64), nullable=False, unique=True)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=now)
    last_used = Column(DateTime(timezone=True))

class Job(Base):
    __tablename__ = 'jobs'
    id = Column(String(36), primary_key=True)
    operation = Column(String(60), nullable=False)
    ref = Column(Text)
    account_id = Column(String(36), ForeignKey('accounts.id'), nullable=True)
    options = Column(JSON, default=dict)
    status = Column(String(20), default='queued')
    result = Column(JSON)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=now)
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)