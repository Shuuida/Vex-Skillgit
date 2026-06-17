from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class GovernanceInfo(BaseModel):
    """
    Pydantic model representing the strict governance boundaries for Vex.
    Used for type hinting and structural validation.
    """
    tenant_id: str = Field(
        ..., 
        min_length=3, 
        description="Unique identifier for the tenant/company."
    )
    skill_id: str = Field(
        ..., 
        min_length=3, 
        description="Target skill this knowledge belongs to."
    )

class DocumentUploadResponse(BaseModel):
    """
    Response schema after successfully parsing a document.
    """
    status: str
    message: str
    total_chunks: int
    prepared_records: list[dict]

class SkillCreateRequest(BaseModel):
    tenant_id: str = Field(..., min_length=3)
    skill_id: str = Field(..., min_length=3)
    name: str = Field(..., min_length=3, description="Human readable name for the skill")
    description: Optional[str] = Field(None, description="Brief explanation of the skill's purpose")

class SkillResponse(BaseModel):
    status: str
    message: str
    data: Optional[dict] = None

class SearchRequest(BaseModel):
    """
    Payload for requesting context from a specific Vex Skill.
    """
    tenant_id: str = Field(..., min_length=3)
    skill_id: str = Field(..., min_length=3)
    query: str = Field(..., min_length=3, description="The semantic question or topic to search for.")
    limit: int = Field(default=3, ge=1, le=10, description="Max number of chunks to return.")
    version: Optional[str] = None

class SearchResult(BaseModel):
    """
    Individual chunk retrieved from the pointer architecture.
    """
    chunk_id: str
    file_path: str
    content: str
    score: float

class SearchResponse(BaseModel):
    """
    Final response containing the synthesized context for the AI agent.
    """
    status: str
    query: str
    results: list[SearchResult]


# Schemas for the GitHub Webhook
class GithubCommit(BaseModel):
    id: str  # This will be the commit_hash
    message: str
    added: List[str] = []
    modified: List[str] = []
    removed: List[str] = []

class GithubWebhookPayload(BaseModel):
    ref: str  # e.g: refs/heads/main
    commits: List[GithubCommit] = []
    repository: Dict[str, Any]

# Schemas for the Documentation Webhook
class DocsWebhookPayload(BaseModel):
    tenant_id: str
    source: str  # e.g: "notion", "confluence", "docusaurus"
    document_id: str
    title: str
    content: str
    version_tag: Optional[str] = "latest"