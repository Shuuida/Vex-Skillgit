import threading
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src.config import QDRANT_URL, QDRANT_API_KEY, COLLECTION_NAME, VECTOR_DIMENSION
from src.logger import get_logger

log = get_logger("db.vector")


class VectorDBManager:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_client(cls):
        """
        Lazy initialization with double-checked locking for thread safety.
        Only connects when called for the first time.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    log.info(f"Initializing connection to Qdrant server on: {QDRANT_URL}")
                    cls._instance = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60.0)

                    if not cls._instance.collection_exists(COLLECTION_NAME):
                        cls._instance.create_collection(
                            collection_name=COLLECTION_NAME,
                            vectors_config=VectorParams(
                                size=VECTOR_DIMENSION,
                                distance=Distance.COSINE
                            ),
                        )
                        log.info(f"Collection '{COLLECTION_NAME}' created successfully.")
                    else:
                        log.info(f"Collection '{COLLECTION_NAME}' already exists. Ready to operate.")
        return cls._instance

    @classmethod
    def close(cls):
        """Close the connection securely."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.close()
                cls._instance = None
                log.info("Connection to Qdrant closed.")

    @classmethod
    def health_check(cls) -> dict:
        """
        Returns health status of the Qdrant vector database.
        Used by the composite /health endpoint.
        """
        try:
            client = cls.get_client()
            collections = client.get_collections()
            return {
                "status": "healthy",
                "collections": [c.name for c in collections.collections]
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


# Helper function for use across the codebase
def get_db_client():
    return VectorDBManager.get_client()