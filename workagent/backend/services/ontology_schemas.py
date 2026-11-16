from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Any


class OntologyRecentScan(BaseModel):
    scan_id: str
    score: Optional[float] = None
    generated_at: Optional[str] = None
    status_counts: dict[str, int] = Field(default_factory=dict)


class OntologyScanResponse(BaseModel):
    scan_id: str
    output_dir: str
    score: float


class OntologyScanJobResponse(BaseModel):
    job_id: str
    status: Literal['queued', 'running', 'completed', 'failed']
    stage: str
    progress: float = 0.0
    scan_id: Optional[str] = None
    output_dir: Optional[str] = None
    score: Optional[float] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    current_batch: Optional[int] = None
    total_batches: Optional[int] = None
    batch_label: Optional[str] = None


class OntologyOverviewResponse(BaseModel):
    latest_scan_id: Optional[str] = None
    standard_graph_path: str
    latest_scan_path: Optional[str] = None
    score: Optional[float] = None
    status_counts: dict[str, int] = Field(default_factory=dict)
    generated_files: list[str] = Field(default_factory=list)
    standard_graph_schema: Optional[str] = None
    standard_graph_counts: dict[str, int] = Field(default_factory=dict)
    recent_scans: list[OntologyRecentScan] = Field(default_factory=list)


class OntologyRoadmapItem(BaseModel):
    node_id: str
    title: str
    domain: str
    status: Literal['partial', 'missing']
    importance_weight: float
    fulfillment_ratio: float
    priority: Literal['immediate', 'high', 'medium', 'low']
    gap_score: float = 0.0
    evidence_count: int = 0
    gap: str = ''
    action: str = ''
    assets: str = ''
    impact: str = ''
    effort: str = 'M'
    evidence_samples: list[str] = Field(default_factory=list)
    rationale: str
    recommendation: str
    ai_object_recommendations: list[str] = Field(default_factory=list)


class OntologyRoadmapResponse(BaseModel):
    items: list[OntologyRoadmapItem]


class OntologyFilePayload(BaseModel):
    data: Any
