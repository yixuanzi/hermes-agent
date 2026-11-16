import apiClient from './client';

export interface OntologyOverview {
  latest_scan_id: string | null;
  standard_graph_path: string;
  latest_scan_path: string | null;
  score: number | null;
  status_counts: Record<string, number>;
  generated_files: string[];
  standard_graph_schema?: string | null;
  standard_graph_counts?: Record<string, number>;
  recent_scans?: Array<{
    scan_id: string;
    score?: number | null;
    generated_at?: string | null;
    status_counts?: Record<string, number>;
  }>;
}

export interface OntologyScanResponse {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  stage: string;
  progress: number;
  scan_id?: string | null;
  output_dir?: string | null;
  score?: number | null;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  current_batch?: number | null;
  total_batches?: number | null;
  batch_label?: string | null;
}

export interface OntologyCompileResponse {
  scan_id: string;
  output_dir: string;
  score: number;
}

export interface OntologyRoadmapItem {
  node_id: string;
  title: string;
  domain: string;
  status: 'partial' | 'missing';
  importance_weight: number;
  fulfillment_ratio: number;
  priority: 'immediate' | 'high' | 'medium' | 'low';
  rationale: string;
  recommendation: string;
  ai_object_recommendations?: string[];
  gap?: string;
  action?: string;
  assets?: string;
  impact?: string;
  effort?: string;
  evidence_samples?: string[];
  evidence_count?: number;
  gap_score?: number;
}

export const ontologyApi = {
  compile: async (): Promise<OntologyCompileResponse> => (await apiClient.post('/ontology/compile')).data,
  scan: async (): Promise<OntologyScanResponse> => (await apiClient.post('/ontology/scan')).data,
  scanStatus: async (jobId: string): Promise<OntologyScanResponse> =>
    (await apiClient.get(`/ontology/scan/${jobId}`)).data,
  /** Ask "is a scan currently running" without already knowing a job_id —
   * lets a freshly (re)mounted page reattach to an in-flight scan after
   * navigating away and back. Throws (404) when nothing is running. */
  scanActive: async (): Promise<OntologyScanResponse> => (await apiClient.get('/ontology/scan/active')).data,
  overview: async (): Promise<OntologyOverview> => (await apiClient.get('/ontology/overview')).data,
  artifact: async (name: 'standard' | 'observed' | 'mapped' | 'scorecard' | 'gap') =>
    (await apiClient.get(`/ontology/artifacts/${name}`)).data.data,
  roadmap: async (): Promise<{ items: OntologyRoadmapItem[] }> => (await apiClient.get('/ontology/roadmap')).data,
};
