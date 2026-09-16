import os
import re
import uuid
import hashlib
from qdrant_client.models import PointStruct, PointIdsList
from qdrant_client.http import models
import urllib.request
import urllib.error
from huey import SqliteHuey
from sqlalchemy import text

from src.config import COLLECTION_NAME, TEMP_UPLOAD_DIR, VEX_DATA_DIR
from src.core.chunker import ast_chunker
from src.core.search import generate_embedding
from src.db.vector import get_db_client, VectorDBManager
from src.db.relational import SessionLocal, ChunkRecord
from src.logger import get_logger
from src.core.manifest import SkillManifestParser

log = get_logger("tasks")

queue_file = os.path.join(VEX_DATA_DIR, "vex_queue.db")
huey = SqliteHuey(filename=queue_file)

@huey.task()
def process_ingestion_task(temp_file_path: str, tenant_id: str, skill_id: str, version: str = "latest", commit_type: str = "standard", memory_tuning: dict = None):
    """
    Executes the ingestion pipeline in a background thread.
    Handles AST chunking, local vectorization via Ollama, and Pointer Architecture storage.
    Now includes semantic 'commit_type' classification and dynamic memory tuning.
    """
    memory_tuning = memory_tuning or {}
    chunk_size = memory_tuning.get("chunk_size", 1000)
    overlap = memory_tuning.get("overlap", 200)

    log.info(f"Task received. Starting ingestion for {temp_file_path} (Tenant: {tenant_id} | Skill: {skill_id} | Intent: {commit_type})")
    
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
            
        log.info(f"Chunking file {filename} with Tree-sitter (Target Size: {chunk_size}, Overlap: {overlap})...")
        raw_chunks = ast_chunker.chunk_source_code(
            source_code, 
            file_extension, 
            chunk_size=chunk_size, 
            overlap=overlap
        )
        
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
                chunk_hash=chunk_hash,
                commit_type=commit_type
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
                    "dependencies": dependencies,
                    "commit_type": commit_type
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

@huey.task()
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

@huey.task()
def process_github_files_task(repo_full_name: str, commit_hash: str, files: list, tenant_id: str, skill_id: str, commit_type: str = "standard", target_version: str = "latest"):
    """
    Background worker that downloads raw files from GitHub and feeds them 
    into the standard ingestion pipeline.
    Now receives the commit intent from the webhook.
    """
    log.info(f"Fetching {len(files)} files from GitHub commit {commit_hash}...")
    os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)

    manifest_config = {}
    if "SKILLS.yaml" in files:
        raw_url = f"https://raw.githubusercontent.com/{repo_full_name}/{commit_hash}/SKILLS.yaml"
        try:
            log.info("SKILLS.yaml detected. Parsing cognitive manifest...")
            req = urllib.request.Request(raw_url)
            with urllib.request.urlopen(req) as response:
                yaml_content = response.read().decode('utf-8')
                
            parser = SkillManifestParser(yaml_content)
            manifest_config = parser.auto_register_skills(tenant_id) 
            
        except Exception as e:
            log.error(f"Failed to parse SKILLS.yaml: {e}")

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
                
            if manifest_config:
                # Iterate over each skill defined in the SKILLS.yaml
                for manifest_skill_id, config in manifest_config.items():
                    boundaries = config.get("context_boundaries", {})
                    includes = boundaries.get("include_extensions", [])
                    excludes = boundaries.get("exclude_paths", [])
                    
                    # Exclusion filter (e.g. discard /backend or .sql)
                    is_excluded = any(ex.replace("*", "") in file_path for ex in excludes)
                    if is_excluded:
                        continue
                        
                    # Inclusion filter (e.g. only .vue and .ts)
                    # If no 'includes' are defined, accept everything by default
                    is_included = not includes or any(file_path.endswith(ext) for ext in includes)
                    
                    if is_included:
                        log.info(f"Routing {filename} to the skill '{manifest_skill_id}'...")
                        tuning = config.get("memory_tuning", {})
                        # Ingest the file into the specific skill
                        process_ingestion_task(
                            temp_path, 
                            tenant_id,
                            skill_id=manifest_skill_id,
                            version=target_version, 
                            commit_type=commit_type,
                            memory_tuning=tuning
                        )
            else:
                # Fallback: Legacy behavior (1 Repo = 1 Skill) if SKILLS.yaml does not exist
                process_ingestion_task(
                    temp_path, 
                    tenant_id, 
                    skill_id=skill_id, 
                    version=target_version, 
                    commit_type=commit_type
                )
            
        except urllib.error.HTTPError as e:
            log.error(f"Failed to fetch {file_path}. HTTP Error: {e.code}")
        except Exception as e:
            log.error(f"Network error fetching {file_path}: {str(e)}")

@huey.task()
def process_rollback_task(tenant_id: str, skill_id: str, target_version: str):
    """
    Executes the cognitive rollback by destroying the current 'latest' pointers 
    and duplicating the historical target_version as the new 'latest'.
    """
    log.info(f"Initiating pointer surgery for {skill_id} (Tenant: {tenant_id}) -> Target: {target_version}")
    
    q_client = VectorDBManager.get_client()
    db = SessionLocal()
    
    try:
        log.info("Step 1: Pruning current 'latest' vectors from Qdrant...")
        q_client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                    models.FieldCondition(key="skill_id", match=models.MatchValue(value=skill_id)),
                    models.FieldCondition(key="version", match=models.MatchValue(value="latest")),
                ]
            )
        )
        
        log.info("Step 2: Archiving 'latest' text records in SQLite...")
        db.execute(
            text("DELETE FROM chunks WHERE tenant_id = :tenant AND skill_id = :skill AND version = 'latest'"),
            {"tenant": tenant_id, "skill": skill_id}
        )
        db.commit()

        log.info(f"Step 3: Fetching historical vectors for version '{target_version}'...")
        records, _ = q_client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                    models.FieldCondition(key="skill_id", match=models.MatchValue(value=skill_id)),
                    models.FieldCondition(key="version", match=models.MatchValue(value=target_version)),
                ]
            ),
            limit=10000,
            with_payload=True,
            with_vectors=True
        )

        if not records:
            raise ValueError(f"Target version '{target_version}' not found in vector memory.")

        log.info("Step 4: Cloning historical memory into new 'latest' pointers...")
        new_points = []
        for record in records:
            new_payload = record.payload.copy()
            new_payload["version"] = "latest"

            if "commit_type" not in new_payload:
                new_payload["commit_type"] = "standard" 
            
            new_points.append(
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=record.vector,
                    payload=new_payload
                )
            )
            
        q_client.upsert(
            collection_name=COLLECTION_NAME,
            points=new_points
        )

        db.execute(
            text("""
                INSERT INTO chunks (chunk_id, tenant_id, skill_id, version, file_path, file_extension, ast_node_type, raw_content, chunk_hash, commit_type)
                SELECT lower(hex(randomblob(16))), tenant_id, skill_id, 'latest', file_path, file_extension, ast_node_type, raw_content, chunk_hash, commit_type
                FROM chunks
                WHERE tenant_id = :tenant AND skill_id = :skill AND version = :target
            """),
            {"tenant": tenant_id, "skill": skill_id, "target": target_version}
        )
        db.commit()
        
        log.info(f"SUCCESS: Cognitive rollback complete. {skill_id} is now mirroring {target_version}.")
        
    except Exception as e:
        db.rollback()
        log.error(f"ERROR during pointer surgery: {str(e)}")
        raise e
    finally:
        db.close()

@huey.task()
def process_branch_task(tenant_id: str, skill_id: str, source_version: str, new_branch_name: str):
    """
    Executes Native Cognitive Branching by cloning the memory pointers of a skill
    into an isolated version environment (fork).
    """
    log.info(f"Initiating Cognitive Branching for {skill_id} (Tenant: {tenant_id}) -> Forking '{source_version}' into '{new_branch_name}'")
    
    q_client = VectorDBManager.get_client()
    db = SessionLocal()
    
    try:
        log.info(f"Step 1: Fetching source vectors for version '{source_version}'...")
        records, _ = q_client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                    models.FieldCondition(key="skill_id", match=models.MatchValue(value=skill_id)),
                    models.FieldCondition(key="version", match=models.MatchValue(value=source_version)),
                ]
            ),
            limit=10000,
            with_payload=True,
            with_vectors=True
        )

        if not records:
            log.warning(f"Source version '{source_version}' is empty or missing. Creating an empty branch '{new_branch_name}'.")
            return

        log.info(f"Step 2: Cloning {len(records)} memory pointers to isolated branch '{new_branch_name}'...")
        new_points = []
        
        for record in records:
            new_payload = record.payload.copy()
            new_payload["version"] = new_branch_name
            
            new_points.append(
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=record.vector, # Cost-free mathematical cloning (zero new vectorization)
                    payload=new_payload
                )
            )
            
        q_client.upsert(
            collection_name=COLLECTION_NAME,
            points=new_points
        )

        log.info("Step 3: Archiving isolated branch state in SQLite...")
        db.execute(
            text("""
                INSERT INTO chunks (chunk_id, tenant_id, skill_id, version, file_path, file_extension, ast_node_type, raw_content, chunk_hash, commit_type)
                SELECT lower(hex(randomblob(16))), tenant_id, skill_id, :new_branch, file_path, file_extension, ast_node_type, raw_content, chunk_hash, commit_type
                FROM chunks
                WHERE tenant_id = :tenant AND skill_id = :skill AND version = :source
            """),
            {"tenant": tenant_id, "skill": skill_id, "new_branch": new_branch_name, "source": source_version}
        )
        db.commit()
        
        log.info(f"SUCCESS: Branching complete. '{new_branch_name}' is now an isolated cognitive reality.")
        
    except Exception as e:
        db.rollback()
        log.error(f"ERROR during cognitive branching: {str(e)}")
        raise e
    finally:
        db.close()