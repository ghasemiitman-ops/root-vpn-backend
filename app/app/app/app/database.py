"""
database.py

DB connection setup. Uses SQLite by default so you can run and test this
locally with zero setup. When you move to your VPS, just set the
DATABASE_URL environment variable to a real Postgres URL and nothing
else needs to change.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rootvpn.db")

# Render (و چند سرویس دیگه) آدرس رو با postgres:// میدن، ولی SQLAlchemy
# جدید postgresql:// می‌خواد. اینجا خودکار درستش می‌کنیم.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
