import os
import sys
from sqlalchemy import create_engine, Column, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker

DB_DIR = os.path.join(os.getcwd(), ".vex_data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "vex_relational.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

# "check_same_thread": False is required for SQLite in FastAPI
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class SkillRecord(Base):
    """
    SQLAlchemy model representing a registered Vex Skill.
    """
    __tablename__ = "skills"

    skill_id = Column(String, primary_key=True, index=True)
    tenant_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)

class ChunkRecord(Base):
    """
    SQLAlchemy model representing the physical storage of a code chunk.
    The chunk_id must match the UUID stored in Qdrant (Pointer Architecture).
    """
    __tablename__ = "chunks"

    chunk_id = Column(String, primary_key=True, index=True)
    tenant_id = Column(String, index=True, nullable=False)
    skill_id = Column(String, index=True, nullable=False)
    file_path = Column(String, nullable=False)
    file_extension = Column(String, nullable=False)
    ast_node_type = Column(String, nullable=False)
    raw_content = Column(Text, nullable=False)
    version = Column(String, default="latest")
    chunk_hash = Column(String, index=True)

def init_relational_db():
    """Creates the tables in the SQLite database if they do not exist."""
    Base.metadata.create_all(bind=engine)
    print(f"[Vex DB] Relational database initialized at: {DB_PATH}", file=sys.stderr)

def get_db_session():
    """Dependency generator for FastAPI route handlers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()