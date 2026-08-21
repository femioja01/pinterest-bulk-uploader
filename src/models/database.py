"""SQLite database models using SQLAlchemy."""

import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, DateTime, Enum, ForeignKey, create_engine, text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from src.config import DB_PATH

Base = declarative_base()


class BatchStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    proxy_url = Column(String(500), nullable=True)
    batch_size = Column(Integer, default=100)
    schedule_cron = Column(String(50), nullable=True)  # Per-account custom cron schedule (e.g. "0 9 */2 * *")
    session_valid = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    batches = relationship("Batch", back_populates="account", cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="account", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Account(name='{self.name}', schedule='{self.schedule_cron}', enabled={self.enabled})>"


class Batch(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=True)
    pin_count = Column(Integer, default=0)
    status = Column(Enum(BatchStatus), default=BatchStatus.PENDING)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    uploaded_at = Column(DateTime, nullable=True)

    account = relationship("Account", back_populates="batches")

    def __repr__(self):
        return f"<Batch(filename='{self.filename}', status={self.status})>"


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)

    def __repr__(self):
        return f"<Setting(key='{self.key}', value='{self.value}')>"


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    event_type = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    account = relationship("Account", back_populates="activity_logs")

    def __repr__(self):
        return f"<ActivityLog(event='{self.event_type}', msg='{self.message[:50]}')>"


# --- Database engine and session ---

def get_engine():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})


def get_session_factory():
    engine = get_engine()
    return sessionmaker(bind=engine)


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)

    # Lightweight migration for existing SQLite DB
    with engine.connect() as conn:
        try:
            result = conn.execute(text("PRAGMA table_info(accounts)"))
            columns = [row[1] for row in result.fetchall()]
            if "schedule_cron" not in columns:
                conn.execute(text("ALTER TABLE accounts ADD COLUMN schedule_cron VARCHAR(50)"))
                conn.commit()
        except Exception:
            pass

    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        _seed_defaults(session)


def _seed_defaults(session: Session):
    from src.config import DEFAULT_UPLOAD_SCHEDULE, DEFAULT_BATCH_SIZE, BATCH_DELAY_SECONDS, TZ

    defaults = {
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "upload_schedule": DEFAULT_UPLOAD_SCHEDULE,
        "batch_delay_seconds": str(BATCH_DELAY_SECONDS),
        "timezone": TZ,
        "default_batch_size": str(DEFAULT_BATCH_SIZE),
        "batches_per_run": "1",
        "notify_on_success": "true",
        "notify_on_failure": "true",
        "notify_on_session_expiry": "true",
        "notify_daily_summary": "true",
    }

    for key, value in defaults.items():
        existing = session.query(Setting).filter_by(key=key).first()
        if not existing:
            session.add(Setting(key=key, value=value))

    session.commit()


def get_setting(session: Session, key: str, default: str = "") -> str:
    setting = session.query(Setting).filter_by(key=key).first()
    return setting.value if setting and setting.value else default


def set_setting(session: Session, key: str, value: str):
    setting = session.query(Setting).filter_by(key=key).first()
    if setting:
        setting.value = value
    else:
        session.add(Setting(key=key, value=value))
    session.commit()
