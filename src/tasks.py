import os
import sys
import uuid
import ollama
import hashlib
from qdrant_client.models import PointStruct, PointIdsList
import urllib.request
import urllib.error

from src.core.chunker import ast_chunker
from src.db.vector import get_db_client
from src.db.relational import SessionLocal, ChunkRecord

def process_ingestion_task(temp_file_path: str, tenant_id: str, skill_id: str, version: str = "latest"):
    """
    Executes the ingestion pipeline in a background thread.
    Handles AST chunking, local vectorization via Ollama, and Pointer Architecture storage.
    """
    print(f"[Worker Thread] Task received. Starting ingestion for {temp_file_path} (Tenant: {tenant_id} | Skill: {skill_id})...", file=sys.stderr)
    
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
        print(f"[Worker Thread] Chunking file {filename} with Tree-sitter...", file=sys.stderr)
        raw_chunks = ast_chunker.chunk_source_code(source_code, file_extension)
        print(f"[Worker Thread] Extracted {len(raw_chunks)} chunks. Generating embeddings...", file=sys.stderr)

        vector_db = get_db_client()
        qdrant_points = []
        
        for chunk in raw_chunks:
            pointer_id = str(uuid.uuid4())
            chunk_content = chunk["content"]
            
            chunk_hash = hashlib.sha256(chunk_content.encode('utf-8')).hexdigest()
            
            # Query the Cognitive Cache (SQLite)
            existing_record = db.query(ChunkRecord).filter(
                ChunkRecord.tenant_id == tenant_id,
                ChunkRecord.chunk_hash == chunk_hash
            ).first()
            
            if existing_record:
                print(f"[Worker Thread] Delta Match: Skipping vectorization for hash {chunk_hash[:8]}", file=sys.stderr)
                
                # We retrieved the old mathematical vector from Qdrant using its ID
                old_point = vector_db.retrieve(
                    collection_name="vex_skills", 
                    ids=[existing_record.chunk_id],
                    with_vectors=True
                )
                
                if old_point and old_point[0].vector:
                    vector_data = old_point[0].vector
                else:
                    # Security fallback in case Qdrant and SQLite become desynchronized
                    response = ollama.embeddings(model="nomic-embed-text", prompt=chunk_content)
                    vector_data = response["embedding"]
            else:
                # Delta Miss: New or modified code. This called Ollama.
                response = ollama.embeddings(model="nomic-embed-text", prompt=chunk_content)
                vector_data = response["embedding"]
            
            record = ChunkRecord(
                chunk_id=pointer_id,
                tenant_id=tenant_id,
                skill_id=skill_id,
                file_path=filename,
                file_extension=file_extension,
                ast_node_type=chunk["ast_node_type"],
                raw_content=chunk["content"],
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
                    "version": version
                }
            )
            qdrant_points.append(point)
            
        # Commit to DBs
        print(f"[Worker Thread] Committing {len(raw_chunks)} records to SQLite...", file=sys.stderr)
        db.commit()
        
        print(f"[Worker Thread] Upserting vectors to Qdrant...", file=sys.stderr)
        vector_db.upsert(collection_name="vex_skills", points=qdrant_points)
        
        print(f"[Worker Thread] SUCCESS: Vectorization complete. Memory committed to Vex.", file=sys.stderr)
        
    except Exception as e:
        db.rollback()
        print(f"[Worker Thread] ERROR processing file {temp_file_path}: {str(e)}", file=sys.stderr)
        
    finally:
        db.close()
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            print(f"[Worker Thread] Cleanup: Temporary file removed.", file=sys.stderr)

def process_deletion_task(file_path: str, tenant_id: str, skill_id: str):
        """
        Erase the phantom memory of a deleted file on GitHub
        deleting their vectors in Qdrant and their pointers in SQLite.
        """
        print(f"[Worker Thread] Task received. Starting PRUNING for {file_path} (Tenant: {tenant_id})...", file=sys.stderr)
        
        db = SessionLocal()
        vector_db = get_db_client()
        
        try:
            # Since the files were saved with temporary names (e.g., gh_87904db_main.py),
            # LIKE is used to find any fragment that ends with the name of this file.
            filename_only = os.path.basename(file_path)
            
            records = db.query(ChunkRecord).filter(
                ChunkRecord.tenant_id == tenant_id,
                ChunkRecord.skill_id == skill_id,
                ChunkRecord.file_path.like(f"%{filename_only}")
            ).all()
            
            if not records:
                print(f"[Worker Thread] Pruning skipped. No ghost chunks found for {file_path}.", file=sys.stderr)
                return
                
            # It extract the exact UUIDs associated with the vector database
            chunk_ids = [record.chunk_id for record in records]
            
            vector_db.delete(
                collection_name="vex_skills",
                points_selector=PointIdsList(points=chunk_ids)
            )          
            for record in records:
                db.delete(record)
                
            db.commit()
            print(f"[Worker Thread] SUCCESS: Annihilated {len(chunk_ids)} phantom chunks of {file_path}.", file=sys.stderr)
            
        except Exception as e:
            db.rollback()
            print(f"[Worker Thread] ERROR pruning file {file_path}: {str(e)}", file=sys.stderr)
        finally:
            db.close()

def process_github_files_task(repo_full_name: str, commit_hash: str, files: list, tenant_id: str, skill_id: str):
    """
    Background worker that downloads raw files from GitHub and feeds them 
    into the standard ingestion pipeline.
    """
    print(f"[Worker Thread] Fetching {len(files)} files from GitHub commit {commit_hash}...", file=sys.stderr)
    os.makedirs("temp_uploads", exist_ok=True)

    for file_path in files:
        # Construct the raw GitHub URL (e.g., https://raw.githubusercontent.com/owner/repo/commit_hash/src/main.py)
        raw_url = f"https://raw.githubusercontent.com/{repo_full_name}/{commit_hash}/{file_path}"
        
        try:
            print(f"[Worker Thread] Downloading {raw_url}...", file=sys.stderr)
            
            # Use Python's native urllib to fetch the file without adding extra dependencies
            req = urllib.request.Request(raw_url)
            with urllib.request.urlopen(req) as response:
                content = response.read()
                
            filename = os.path.basename(file_path)
            temp_path = os.path.join("temp_uploads", f"gh_{commit_hash[:7]}_{filename}")
            
            with open(temp_path, "wb") as f:
                f.write(content)
                
            # Feed the downloaded file into our standard vectorization engine
            process_ingestion_task(temp_path, tenant_id, skill_id, version=commit_hash)
            
        except urllib.error.HTTPError as e:
            print(f"[Worker Thread] Failed to fetch {file_path}. HTTP Error: {e.code}", file=sys.stderr)
        except Exception as e:
            print(f"[Worker Thread] Network error fetching {file_path}: {str(e)}", file=sys.stderr)