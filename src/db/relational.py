import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from src.config import VEX_DATA_DIR
from src.logger import get_logger

log = get_logger("db.relational")

Base = declarative_base()

os.makedirs(VEX_DATA_DIR, exist_ok=True)
db_path = os.path.join(VEX_DATA_DIR, "vex_relational.db")
database_url = f"sqlite:///{db_path}"

_engine = create_engine(database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


class SkillRecord(Base):
    """
    SQLAlchemy model representing a registered Vex Skill.
    """
    __tablename__ = "skills"

    skill_id = Column(String, primary_key=True, index=True)
    tenant_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    chunks = relationship("ChunkRecord", back_populates="skill", passive_deletes=True)


class ChunkRecord(Base):
    """
    SQLAlchemy model representing the physical storage of a code chunk.
    The chunk_id must match the UUID stored in Qdrant (Pointer Architecture).
    """
    __tablename__ = "chunks"

    chunk_id = Column(String, primary_key=True, index=True)
    tenant_id = Column(String, index=True, nullable=False)
    skill_id = Column(String, ForeignKey("skills.skill_id"), index=True, nullable=False)
    file_path = Column(String, nullable=False)
    file_extension = Column(String, nullable=False)
    ast_node_type = Column(String, nullable=False)
    raw_content = Column(Text, nullable=False)
    version = Column(String, default="latest")
    chunk_hash = Column(String, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    skill = relationship("SkillRecord", back_populates="chunks")


def init_relational_db():
    """
    Lazily initializes the SQLite engine, session factory, and creates tables.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    Base.metadata.create_all(bind=_engine)
    log.info(f"Relational database tables verified at: {db_path}")

def get_db_session():
    """Dependency generator for FastAPI route handlers."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_relational_db() first.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()