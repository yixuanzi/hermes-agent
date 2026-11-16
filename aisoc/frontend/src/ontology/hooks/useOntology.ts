import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ontologyApi, type OntologyScanResponse } from '../api/ontology';

export const useOntologyOverview = () =>
  useQuery({ queryKey: ['ontologyOverview'], queryFn: ontologyApi.overview });
export const useOntologyArtifact = (name: 'standard' | 'observed' | 'mapped' | 'scorecard' | 'gap') =>
  useQuery({ queryKey: ['ontologyArtifact', name], queryFn: () => ontologyApi.artifact(name) });
export const useOntologyRoadmap = () =>
  useQuery({ queryKey: ['ontologyRoadmap'], queryFn: ontologyApi.roadmap });

export const useOntologyCompile = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ontologyApi.compile,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ontologyOverview'] });
      qc.invalidateQueries({ queryKey: ['ontologyArtifact'] });
    },
  });
};

// AI 扫描现在走异步 job：起 job → 轮询到 completed/failed 再解析结果。
// 页面若要显示实时进度，走 DifferentiationOverviewPage 里的 runScan 手动轮询。
export const useOntologyScan = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<OntologyScanResponse> => {
      const job = await ontologyApi.scan();
      let latest = job;
      for (let i = 0; i < 180; i += 1) {
        if (latest.status === 'completed' || latest.status === 'failed') break;
        await new Promise((r) => setTimeout(r, 1000));
        latest = await ontologyApi.scanStatus(job.job_id);
      }
      if (latest.status === 'failed') throw new Error(latest.error || 'scan failed');
      if (latest.status !== 'completed') throw new Error('scan polling timeout');
      return latest;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ontologyOverview'] });
      qc.invalidateQueries({ queryKey: ['ontologyArtifact'] });
      qc.invalidateQueries({ queryKey: ['ontologyRoadmap'] });
    },
  });
};
