from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
import os
import sys

DB_PATH = os.path.join(os.getcwd(), ".qdrant_data")
COLLECTION_NAME = "vex_skills"

class VectorDBManager:
    _instance = None

    @classmethod
    def get_client(cls):
        """Lazy initialization. Only connects when called for the first time."""
        if cls._instance is None:
            print(f"[Vex DB] Initializing connection to Qdrant Local on: {DB_PATH}", file=sys.stderr)
            cls._instance = QdrantClient(path=DB_PATH)
            
            # We check if the collection exists
            if not cls._instance.collection_exists(COLLECTION_NAME):
                cls._instance.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
                )
                print(f"[Vex DB] Collection '{COLLECTION_NAME}' created successfully.", file=sys.stderr)
            else:
                print(f"[Vex DB] Collection '{COLLECTION_NAME}' it already exists. Ready to operate.", file=sys.stderr)
        return cls._instance

    @classmethod
    def close(cls):
        """Close the connection securely."""
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None
            print("[Vex DB] Connection to Qdrant closed.", file=sys.stderr)

# Helper function for use in FastAPI
def get_db_client():
    return VectorDBManager.get_client()