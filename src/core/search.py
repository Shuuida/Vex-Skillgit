import asyncio
from ollama import AsyncClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from src.config import (
    EMBEDDING_MODEL, COLLECTION_NAME, SCORE_THRESHOLD,
    DEFAULT_SEARCH_LIMIT, QUERY_ENHANCEMENT_PREFIX,
    EMBEDDING_MAX_RETRIES, EMBEDDING_RETRY_DELAY
)
from src.db.vector import get_db_client
from src.db.relational import SessionLocal, ChunkRecord
from src.logger import get_logger

log = get_logger("search")


async def generate_embedding(text: str) -> list[float]:
    """
    Generate a vector asynchronously so as not to block the FastAPI Event Loop.
    Use asyncio.sleep for exponential backoff.
    """
    last_error = None
    client = AsyncClient()
    
    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            response = await client.embeddings(model=EMBEDDING_MODEL, prompt=text)
            return response["embedding"]
        except Exception as e:
            last_error = e
            if attempt < EMBEDDING_MAX_RETRIES:
                delay = EMBEDDING_RETRY_DELAY * (2 ** (attempt - 1))
                log.warning(f"Embedding attempt {attempt}/{EMBEDDING_MAX_RETRIES} failed: {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                
    raise RuntimeError(f"Embedding generation failed after {EMBEDDING_MAX_RETRIES} attempts: {last_error}")


async def search_skill(tenant_id: str, skill_id: str, query: str, version: str | None = None, limit: int = DEFAULT_SEARCH_LIMIT, score_threshold: float = SCORE_THRESHOLD) -> list[dict]:
    """
    Unified and asynchronous search pipeline.
    Optimized to avoid N+1 queries in SQLite and audit the pointer invariant.
    """
    enhanced_query = f"{QUERY_ENHANCEMENT_PREFIX} {query}"
    query_vector = await generate_embedding(enhanced_query)
    
    vector_db = get_db_client()
    must_conditions = [
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
        FieldCondition(key="skill_id", match=MatchValue(value=skill_id))
    ]
    
    if version:
        must_conditions.append(
            FieldCondition(key="version", match=MatchValue(value=version))
        )
    
    search_filter = Filter(must=must_conditions)
    
    qdrant_response = vector_db.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=search_filter,
        limit=limit,
        score_threshold=score_threshold
    )
    qdrant_results = qdrant_response.points
    
    if not qdrant_results:
        return []
    
    db = SessionLocal()
    results = []
    
    try:
        point_ids = [str(hit.id) for hit in qdrant_results]
        
        records = db.query(ChunkRecord).filter(ChunkRecord.chunk_id.in_(point_ids)).all()
        record_map = {record.chunk_id: record for record in records}
        
        for hit in qdrant_results:
            chunk_id = str(hit.id)
            record = record_map.get(chunk_id)
            
            if record:
                results.append({
                    "chunk_id": chunk_id,
                    "file_path": record.file_path,
                    "content": record.raw_content,
                    "score": hit.score
                })
            else:
                log.error(f"Orphan vector detected: The vector '{chunk_id}' exists in Qdrant but SQLite does not have the source text.")
                
    finally:
        db.close()
    
    log.debug(f"Search for '{query}' returned {len(results)} results (tenant={tenant_id}, skill={skill_id})")
    return results