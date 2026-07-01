"""
Centralized configuration for Vex.
All hardcoded values are consolidated here with environment variable overrides.
"""
import os
from pathlib import Path

# --- Project Paths ---
# Use the project root (two levels up from this file) instead of os.getcwd()
# to avoid fragile behavior when the process is launched from a different directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

VEX_DATA_DIR = os.environ.get(
    "VEX_DATA_DIR", str(PROJECT_ROOT / ".vex_data")
)
QDRANT_PATH = os.environ.get(
    "VEX_QDRANT_PATH", str(PROJECT_ROOT / ".qdrant_data")
)
TEMP_UPLOAD_DIR = os.environ.get(
    "VEX_TEMP_DIR", str(PROJECT_ROOT / "temp_uploads")
)

# --- Embedding Configuration ---
EMBEDDING_MODEL = os.environ.get("VEX_EMBEDDING_MODEL", "nomic-embed-text")
VECTOR_DIMENSION = int(os.environ.get("VEX_VECTOR_DIMENSION", "768"))

# --- Vector Database ---
COLLECTION_NAME = os.environ.get("VEX_COLLECTION_NAME", "vex_skills")
SCORE_THRESHOLD = float(os.environ.get("VEX_SCORE_THRESHOLD", "0.60"))

# --- Search ---
DEFAULT_SEARCH_LIMIT = int(os.environ.get("VEX_DEFAULT_SEARCH_LIMIT", "3"))
QUERY_ENHANCEMENT_PREFIX = os.environ.get(
    "VEX_QUERY_PREFIX",
    "source code, function definition, class, method, technical implementation of:"
)

# --- GitHub Webhook ---
GITHUB_WEBHOOK_SECRET = os.environ.get("VEX_GITHUB_WEBHOOK_SECRET")
GITHUB_ALLOWED_REFS = os.environ.get(
    "VEX_GITHUB_ALLOWED_REFS", "refs/heads/main,refs/heads/master"
).split(",")

# --- Security ---
REQUIRE_AUTH = os.environ.get("VEX_REQUIRE_AUTH", "False").lower() == "true"
API_KEY = os.environ.get("VEX_API_KEY")

# --- Logging ---
LOG_LEVEL = os.environ.get("VEX_LOG_LEVEL", "INFO").upper()

# --- Reliability ---
EMBEDDING_MAX_RETRIES = int(os.environ.get("VEX_EMBEDDING_MAX_RETRIES", "3"))
EMBEDDING_RETRY_DELAY = float(os.environ.get("VEX_EMBEDDING_RETRY_DELAY", "1.0"))

# --- File Extensions ---
ALLOWED_UPLOAD_EXTENSIONS = {
    ".py", ".ts", ".js", ".tsx", ".jsx", ".go",
    ".yml", ".yaml", ".md"
}
