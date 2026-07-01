import os
import re
import uuid
import hashlib
from qdrant_client.models import PointStruct, PointIdsList
import urllib.request
import urllib.error

from src.config import COLLECTION_NAME, TEMP_UPLOAD_DIR
from src.core.chunker import ast_chunker
from src.core.search import generate_embedding
from src.db.vector import get_db_client
from src.db.relational import SessionLocal, ChunkRecord
from src.logger import get_logger

log = get_logger("tasks")


def process_ingestion_task(temp_file_path: str, tenant_id: str, skill_id: str, version: str = "latest"):
    """
    Executes the ingestion pipeline in a background thread.
    Handles AST chunking, local vectorization via Ollama, and Pointer Architecture storage.
    """
    log.info(f"Task received. Starting ingestion for {temp_file_path} (Tenant: {tenant_id} | Skill: {skill_id})")
    
    db = SessionLocal()
    
    try:
        filename = os.path.basename(temp_file_path)
        file_extension = os.path.splitext(filename)[1].lower()
        
        with open(temp_file_path, "rb") as f:
            content_bytes = f.read()
            
        try:
            source_code = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Only UTF-8 encoded text files are supported.")
            
        # AST Chunking
        log.info(f"Chunking file {filename} with Tree-sitter...")
        raw_chunks = ast_chunker.chunk_source_code(source_code, file_extension)
        log.info(f"Extracted {len(raw_chunks)} chunks. Generating embeddings...")

        vector_db = get_db_client()
        qdrant_points = []
        
        for chunk in raw_chunks:
            pointer_id = str(uuid.uuid4())
            chunk_content = chunk["content"]
            dependencies = chunk.get("dependencies", [])
            
            chunk_hash = hashlib.sha256(chunk_content.encode('utf-8')).hexdigest()

            if dependencies:
                graph_context = f"# [GraphRAG] Calls/Dependencies: {', '.join(dependencies)}\n"
                enriched_content = graph_context + chunk_content
            else:
                enriched_content = chunk_content
            
            # Query the Cognitive Cache (SQLite) — scoped to skill + version
            existing_record = db.query(ChunkRecord).filter(
                ChunkRecord.tenant_id == tenant_id,
                ChunkRecord.skill_id == skill_id,
                ChunkRecord.version == version,
                ChunkRecord.chunk_hash == chunk_hash
            ).first()
            
            if existing_record:
                log.info(f"Delta Match: Skipping vectorization. Perfect cache hit for hash {chunk_hash[:8]}. Skipping entirely")
                continue

            vector_data = generate_embedding(chunk_content)
            
            record = ChunkRecord(
                chunk_id=pointer_id,
                tenant_id=tenant_id,
                skill_id=skill_id,
                file_path=filename,
                file_extension=file_extension,
                ast_node_type=chunk["ast_node_type"],
                raw_content=enriched_content,
                version=version,
                chunk_hash=chunk_hash
            )
            db.add(record)
            
            point = PointStruct(
                id=pointer_id,
                vector=vector_data,
                payload={
                    "tenant_id": tenant_id,
                    "skill_id": skill_id,
                    "file_path": filename,
                    "ast_node_type": chunk["ast_node_type"],
                    "version": version,
                    "dependencies": dependencies
                }
            )
            qdrant_points.append(point)

        log.info(f"Upserting {len(qdrant_points)} vectors to Qdrant...")
        vector_db.upsert(collection_name=COLLECTION_NAME, points=qdrant_points)
        
        log.info(f"Committing {len(raw_chunks)} records to SQLite...")
        try:
            db.commit()
        except Exception:
            point_ids = [p.id for p in qdrant_points]
            vector_db.delete(
                collection_name=COLLECTION_NAME,
                points_selector=PointIdsList(points=point_ids)
            )
            raise
        
        log.info("SUCCESS: Vectorization complete. Memory committed to Vex.")
        
    except Exception as e:
        db.rollback()
        log.error(f"ERROR processing file {temp_file_path}: {str(e)}")
        
    finally:
        db.close()
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            log.debug("Cleanup: Temporary file removed.")

def process_deletion_task(file_path: str, tenant_id: str, skill_id: str):
    """
    Erase the phantom memory of a deleted file on GitHub
    deleting their vectors in Qdrant and their pointers in SQLite.
    """
    log.info(f"Task received. Starting PRUNING for {file_path} (Tenant: {tenant_id})")
    
    db = SessionLocal()
    vector_db = get_db_client()
    
    try:
        # Since the files were saved with temporary names (e.g., gh_87904db_main.py),
        # LIKE is used to find any fragment that ends with the name of this file.
        filename_only = os.path.basename(file_path)
        
        # Escape LIKE meta-characters to prevent wildcard injection
        safe_filename = filename_only.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        
        records = db.query(ChunkRecord).filter(
            ChunkRecord.tenant_id == tenant_id,
            ChunkRecord.skill_id == skill_id,
            ChunkRecord.file_path.like(f"%{safe_filename}", escape='\\')
        ).all()
        
        if not records:
            log.info(f"Pruning skipped. No ghost chunks found for {file_path}.")
            return
            
        # Extract the exact UUIDs associated with the vector database
        chunk_ids = [record.chunk_id for record in records]
        
        vector_db.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=chunk_ids)
        )
        for record in records:
            db.delete(record)
            
        db.commit()
        log.info(f"SUCCESS: Annihilated {len(chunk_ids)} phantom chunks of {file_path}.")
        
    except Exception as e:
        db.rollback()
        log.error(f"ERROR pruning file {file_path}: {str(e)}")
    finally:
        db.close()

def process_github_files_task(repo_full_name: str, commit_hash: str, files: list, tenant_id: str, skill_id: str):
    """
    Background worker that downloads raw files from GitHub and feeds them 
    into the standard ingestion pipeline.
    """
    log.info(f"Fetching {len(files)} files from GitHub commit {commit_hash}...")
    os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)

    for file_path in files:
        # Construct the raw GitHub URL
        raw_url = f"https://raw.githubusercontent.com/{repo_full_name}/{commit_hash}/{file_path}"
        
        try:
            log.info(f"Downloading {raw_url}...")
            
            # Use Python's native urllib to fetch the file without adding extra dependencies
            req = urllib.request.Request(raw_url)
            with urllib.request.urlopen(req) as response:
                content = response.read()
                
            filename = os.path.basename(file_path)
            temp_path = os.path.join(TEMP_UPLOAD_DIR, f"gh_{commit_hash[:7]}_{filename}")
            
            with open(temp_path, "wb") as f:
                f.write(content)
                
            # Feed the downloaded file into our standard vectorization engine
            process_ingestion_task(temp_path, tenant_id, skill_id, version=commit_hash)
            
        except urllib.error.HTTPError as e:
            log.error(f"Failed to fetch {file_path}. HTTP Error: {e.code}")
        except Exception as e:
            log.error(f"Network error fetching {file_path}: {str(e)}")
