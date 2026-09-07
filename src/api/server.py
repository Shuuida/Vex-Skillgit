import os
import uuid
import hmac
import hashlib
import shutil
import re
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

from src.config import ALLOWED_UPLOAD_EXTENSIONS, GITHUB_ALLOWED_REFS, TEMP_UPLOAD_DIR
from src.db.vector import VectorDBManager
from src.core.chunker import ast_chunker
from src.core.search import search_skill as _search_skill
from src.db.relational import init_relational_db, SessionLocal, SkillRecord
from src.api.schemas import SkillCreateRequest, SkillResponse, SearchRequest, SearchResponse, SearchResult, GithubWebhookPayload, DocsWebhookPayload, RollbackRequest
from src.tasks import process_ingestion_task, process_github_files_task, process_deletion_task, process_rollback_task
from src.api.security import verify_api_key
from src.logger import get_logger

log = get_logger("api")

def _sanitize_filename(filename: str) -> str:
    """Strip path components and dangerous characters from an uploaded filename."""
    basename = os.path.basename(filename)
    return re.sub(r'[^a-zA-Z0-9_.\-]', '_', basename)

async def verify_github_signature(request: Request):
    """
    FastAPI dependency that verifies the GitHub webhook HMAC-SHA256 signature.
    Disabled when VEX_GITHUB_WEBHOOK_SECRET is not set (local development).
    """
    secret = os.environ.get("VEX_GITHUB_WEBHOOK_SECRET")
    if not secret:
        return
    
    signature_header = request.headers.get("X-Hub-Signature-256")
    if not signature_header:
        raise HTTPException(status_code=403, detail="Missing X-Hub-Signature-256 header.")
    
    body = await request.body()
    expected_signature = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature_header, expected_signature):
        raise HTTPException(status_code=403, detail="Invalid webhook signature.")

# Pydantic schema for the testing endpoint
class ParseRequest(BaseModel):
    source_code: str
    file_extension: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_relational_db()
    VectorDBManager.get_client()
    log.info("Vex API started successfully.")
    yield
    VectorDBManager.close()
    log.info("Vex API shutdown complete.")

app = FastAPI(
    title="Vex API",
    description="Headless Skill Hub for AI Agents",
    version="0.1.0",
    lifespan=lifespan
)

# allows cross-origin agent and frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("VEX_CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "status": "online", 
        "system": "Vex Core", 
        "message": "Operating Memory Gateway"
    }

@app.get("/health")
async def health():
    """Composite health endpoint — checks both Qdrant and SQLite."""
    qdrant_health = VectorDBManager.health_check()
    
    sqlite_status = "healthy"
    try:
        if SessionLocal is not None:
            db = SessionLocal()
            db.execute("SELECT 1")
            db.close()
        else:
            sqlite_status = "not_initialized"
    except Exception as e:
        sqlite_status = f"unhealthy: {e}"
    
    overall = "healthy" if qdrant_health["status"] == "healthy" and sqlite_status == "healthy" else "degraded"
    
    return {
        "status": overall,
        "qdrant": qdrant_health,
        "sqlite": {"status": sqlite_status}
    }

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

@app.post("/skills/create", response_model=SkillResponse, dependencies=[Depends(verify_api_key)])
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

@app.get("/skills/{skill_id}", response_model=SkillResponse, dependencies=[Depends(verify_api_key)])
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

@app.post("/documents/upload", dependencies=[Depends(verify_api_key)])
async def upload_document(
    file: UploadFile = File(...),
    tenant_id: str = Form(..., min_length=3),
    skill_id: str = Form(..., min_length=3),
    version: str = Form("latest")
):
    """
    Receives a physical file, saves it temporarily, and dispatches an asynchronous 
    background thread for processing. Returns an immediate 202 Accepted response.
    Validates file extension against ALLOWED_EXTENSIONS and sanitizes the filename.
    """
    raw_filename = file.filename or "unknown_file"
    safe_filename = _sanitize_filename(raw_filename)
    ext = os.path.splitext(safe_filename)[1].lower()
    
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File extension '{ext}' is not supported. Allowed: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}"
        )
    
    os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
    unique_filename = f"{uuid.uuid4().hex[:8]}_{safe_filename}"
    temp_path = os.path.join(TEMP_UPLOAD_DIR, unique_filename)
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    process_ingestion_task(temp_path, tenant_id, skill_id, version)
    
    return JSONResponse(
        status_code=202,
        content={
            "message": "Document received successfully.",
            "status": "processing_in_background",
            "tenant_id": tenant_id,
            "skill_id": skill_id,
            "version": version,
            "filename": safe_filename
        }
    )

@app.post("/skills/search", response_model=SearchResponse, dependencies=[Depends(verify_api_key)])
async def search_skill_endpoint(request: SearchRequest):
    """
    Vectorizes the query, searches Qdrant with governance filters, 
    and retrieves the heavy text from SQLite (Pointer Architecture).
    Delegates to the shared search service.
    """
    try:
        results = _search_skill(
            tenant_id=request.tenant_id,
            skill_id=request.skill_id,
            query=request.query,
            version=request.version,
            limit=request.limit
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

    return SearchResponse(
        status="success",
        query=request.query,
        results=[
            SearchResult(
                chunk_id=r["chunk_id"],
                file_path=r["file_path"],
                content=r["content"],
                score=r["score"]
            ) for r in results
        ]
    )

@app.post("/skills/rollback", dependencies=[Depends(verify_api_key)])
async def rollback_skill_endpoint(request: RollbackRequest):
    """
    Restores a skill's 'latest' memory pointers to a historical target version.
    Delegates the heavy pointer manipulation to the Huey worker.
    """
    process_rollback_task(
        request.tenant_id,
        request.skill_id,
        request.target_version
    )
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "processing_in_background",
            "message": f"Rollback initiated for skill '{request.skill_id}'.",
            "tenant_id": request.tenant_id,
            "target_version": request.target_version
        }
    )

@app.post("/webhooks/github", dependencies=[Depends(verify_github_signature)])
async def github_webhook(payload: GithubWebhookPayload):
    """
    Receives push events directly from GitHub.
    Verifies HMAC-SHA256 signature, filters by allowed branches,
    and dynamically assigns tenant_id based on the repository owner.
    """
    if payload.ref not in GITHUB_ALLOWED_REFS:
        return JSONResponse(
            status_code=200,
            content={
                "message": f"Ignored push to non-default branch: {payload.ref}",
                "status": "skipped"
            }
        )
    
    repo_name = payload.repository.get("name", "unknown_repo")
    repo_full_name = payload.repository.get("full_name", repo_name)
    
    # (e.g. "facebook/react" -> "facebook" is owner)
    owner_login = payload.repository.get("owner", {}).get("login", "unknown_owner")
    
    # Clean the string for security before using it in the database
    safe_owner = re.sub(r'[^a-zA-Z0-9_\-]', '', owner_login).lower()
    dynamic_tenant_id = f"tnt_gh_{safe_owner}"
    
    processed_commits = []
    
    for commit in payload.commits:
        commit_hash = commit.id
        files_to_download = commit.added + commit.modified
        files_to_delete = commit.removed

        # The skill will always be associated with this repository
        skill_id = f"repo_{repo_name}"
        
        if files_to_download:
            process_github_files_task(
                repo_full_name,
                commit_hash,
                files_to_download,
                dynamic_tenant_id,
                skill_id
            )
            
        if files_to_delete:
            for file_path in files_to_delete:
                process_deletion_task(
                    file_path,
                    dynamic_tenant_id,
                    skill_id
                )
            
        processed_commits.append({
            "hash": commit_hash,
            "files_changed": len(files_to_download),
            "files_removed": len(files_to_delete)
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

@app.post("/webhooks/docs", dependencies=[Depends(verify_api_key)])
async def docs_webhook(payload: DocsWebhookPayload):
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
    
    process_ingestion_task( 
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