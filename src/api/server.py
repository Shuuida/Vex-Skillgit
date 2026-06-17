import os
import uuid
import shutil
import ollama
import re
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from src.db.vector import get_db_client, VectorDBManager
from src.core.chunker import ast_chunker
from src.db.relational import init_relational_db, SessionLocal, ChunkRecord, SkillRecord
from src.api.schemas import DocumentUploadResponse, SkillCreateRequest, SkillResponse, SearchRequest, SearchResponse, SearchResult, GithubWebhookPayload, DocsWebhookPayload
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue
from src.tasks import process_ingestion_task, process_github_files_task

# Pydantic schema for the testing endpoint
class ParseRequest(BaseModel):
    source_code: str
    file_extension: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_relational_db()
    get_db_client()
    yield
    VectorDBManager.close()

app = FastAPI(
    title="Vex API",
    description="Headless Skill Hub for AI Agents",
    version="0.1.0",
    lifespan=lifespan
)

@app.get("/")
async def root():
    return {
        "status": "online", 
        "system": "Vex Core", 
        "message": "Operating Memory Gateway"
    }

@app.get("/health/db")
async def db_health():
    """Verifies if Qdrant is responding."""
    try:
        client = get_db_client()
        collections = client.get_collections()
        return {
            "status": "healthy",
            "collections": [c.name for c in collections.collections]
        }
    except Exception as e:
        return {"status": "unhealthy", "message": str(e)}

@app.post("/test/parse")
async def test_ast_parsing(request: ParseRequest):
    """Temporary endpoint to test Tree-sitter chunking capabilities."""
    try:
        chunks = ast_chunker.chunk_source_code(
            source_code=request.source_code, 
            file_extension=request.file_extension
        )
        return {
            "status": "success",
            "total_chunks_extracted": len(chunks),
            "chunks": chunks
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/skills/create", response_model=SkillResponse)
async def create_skill(request: SkillCreateRequest):
    """Registers a new Skill in the relational database."""
    db = SessionLocal()
    try:
        # Check if skill already exists
        existing_skill = db.query(SkillRecord).filter(SkillRecord.skill_id == request.skill_id).first()
        if existing_skill:
            return SkillResponse(
                status="error", 
                message=f"Skill '{request.skill_id}' already exists."
            )
        
        new_skill = SkillRecord(
            skill_id=request.skill_id,
            tenant_id=request.tenant_id,
            name=request.name,
            description=request.description
        )
        db.add(new_skill)
        db.commit()
        
        return SkillResponse(
            status="success",
            message="Skill registered successfully.",
            data={"skill_id": request.skill_id, "name": request.name}
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/skills/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str):
    """Retrieves the metadata of a specific Skill."""
    db = SessionLocal()
    try:
        skill = db.query(SkillRecord).filter(SkillRecord.skill_id == skill_id).first()
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
            
        return SkillResponse(
            status="success",
            message="Skill retrieved.",
            data={
                "skill_id": skill.skill_id,
                "tenant_id": skill.tenant_id,
                "name": skill.name,
                "description": skill.description
            }
        )
    finally:
        db.close()

@app.post("/documents/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    tenant_id: str = Form(..., min_length=3),
    skill_id: str = Form(..., min_length=3),
    version: str = Form("latest")
):
    """
    Receives a physical file, saves it temporarily, and dispatches an asynchronous 
    background thread for processing. Returns an immediate 202 Accepted response.
    """
    os.makedirs("temp_uploads", exist_ok=True)
    filename = file.filename or "unknown_file"
    temp_path = os.path.join("temp_uploads", filename)
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    background_tasks.add_task(process_ingestion_task, temp_path, tenant_id, skill_id, version)
    
    return JSONResponse(
        status_code=202,
        content={
            "message": "Document received successfully.",
            "status": "processing_in_background",
            "tenant_id": tenant_id,
            "skill_id": skill_id,
            "version": version,
            "filename": filename
        }
    )

@app.post("/skills/search", response_model=SearchResponse)
async def search_skill(request: SearchRequest):
    """
    Vectorizes the query, searches Qdrant with governance filters, 
    and retrieves the heavy text from SQLite (Pointer Architecture).
    """
    # Vectorize the semantic query
    try:
        response = ollama.embeddings(model="nomic-embed-text", prompt=request.query)
        query_vector = response["embedding"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding generation failed: {str(e)}")

    vector_db = get_db_client()
    must_conditions = [
        FieldCondition(key="tenant_id", match=MatchValue(value=request.tenant_id)),
        FieldCondition(key="skill_id", match=MatchValue(value=request.skill_id))
    ]
    
    # If the user or agent requested a specific version, we added it to the mathematical condition.
    if request.version:
        must_conditions.append(
            FieldCondition(key="version", match=MatchValue(value=request.version))
        )

    search_filter = Filter(must=must_conditions)

    try:
        qdrant_response = vector_db.query_points(
            collection_name="vex_skills",
            query=query_vector,
            query_filter=search_filter,
            limit=request.limit
        )
        # Extract the list of hits from the response wrapper
        qdrant_results = qdrant_response.points
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vector database search failed: {str(e)}")

    if not qdrant_results:
        return SearchResponse(status="success", query=request.query, results=[])

    # Retrieve Heavy Text from Relational DB
    db = SessionLocal()
    final_results = []
    
    try:
        for hit in qdrant_results:
            chunk_id = str(hit.id)
            # Find the actual text payload using the Qdrant ID
            record = db.query(ChunkRecord).filter(ChunkRecord.chunk_id == chunk_id).first()
            
            if record:
                final_results.append(
                    SearchResult(
                        chunk_id=chunk_id,
                        file_path=record.file_path,
                        content=record.raw_content,
                        score=hit.score
                    )
                )
    finally:
        db.close()

    return SearchResponse(
        status="success",
        query=request.query,
        results=final_results
    )

@app.post("/webhooks/github")
async def github_webhook(
    payload: GithubWebhookPayload,
    background_tasks: BackgroundTasks
):
    """
    Receives push events directly from GitHub. 
    Dynamically assigns tenant_id based on the repository owner.
    """
    repo_name = payload.repository.get("name", "unknown_repo")
    repo_full_name = payload.repository.get("full_name", repo_name)
    
    # (e.g. "facebook/react" -> "facebook" is owner)
    owner_login = payload.repository.get("owner", {}).get("login", "unknown_owner")
    
    # This clean the string for security before using it in the database
    safe_owner = re.sub(r'[^a-zA-Z0-9_\-]', '', owner_login).lower()
    dynamic_tenant_id = f"tnt_gh_{safe_owner}"
    
    processed_commits = []
    
    for commit in payload.commits:
        commit_hash = commit.id 
        files_to_download = commit.added + commit.modified
        
        if files_to_download:
            # The skill is dynamically associated with the specific repository
            skill_id = f"repo_{repo_name}" 
            
            background_tasks.add_task(
                process_github_files_task,
                repo_full_name,
                commit_hash,
                files_to_download,
                dynamic_tenant_id, 
                skill_id
            )
        
        processed_commits.append({
            "hash": commit_hash,
            "files_changed": len(files_to_download)
        })

    return JSONResponse(
        status_code=202,
        content={
            "message": "GitHub Webhook received.",
            "status": "processing_commits_in_background",
            "assigned_tenant": dynamic_tenant_id,
            "repository": repo_name,
            "commits_acknowledged": processed_commits
        }
    )

@app.post("/webhooks/docs")
async def docs_webhook(
    payload: DocsWebhookPayload,
    background_tasks: BackgroundTasks
):
    """
    Receives raw text payloads from documentation platforms.
    Uses the dynamically provided tenant_id from the external system.
    """
    os.makedirs("temp_uploads", exist_ok=True)
    
    safe_title = re.sub(r'[^a-zA-Z0-9_\- ]', '', payload.title).strip().replace(" ", "_")
    filename = f"{safe_title}_{payload.version_tag}.md"
    temp_path = os.path.join("temp_uploads", filename)
    
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(payload.content)
        
    safe_tenant = re.sub(r'[^a-zA-Z0-9_\-]', '', payload.tenant_id).lower()
    dynamic_tenant_id = f"tnt_docs_{safe_tenant}"
    
    dynamic_skill_id = f"docs_{payload.source}"
    
    background_tasks.add_task(
        process_ingestion_task, 
        temp_path, 
        dynamic_tenant_id,
        dynamic_skill_id,
        payload.version_tag
    )
    
    return JSONResponse(
        status_code=202,
        content={
            "message": "Documentation Webhook received.",
            "status": "processing_in_background",
            "assigned_tenant": dynamic_tenant_id,
            "source": payload.source,
            "document": payload.title,
            "version": payload.version_tag
        }
    )