import { fetchJSON } from "./api";

export type TreeItem = {
  name: string;
  path: string;
  type: "dir" | "file";
  size: number;
  modified: number;
};

export type TreeResponse = {
  root: string;
  cwd: string;
  items: TreeItem[];
};

export type DocumentResponse = {
  name: string;
  path: string;
  size: number;
  modified: number;
  content: string;
};

export function fetchTree(cwd?: string): Promise<TreeResponse> {
  const query = cwd ? `?cwd=${encodeURIComponent(cwd)}` : "";
  return fetchJSON<TreeResponse>(`/api/kb/tree${query}`);
}

export function fetchDocument(path: string): Promise<DocumentResponse> {
  return fetchJSON<DocumentResponse>(
    `/api/kb/documents?path=${encodeURIComponent(path)}`,
  );
}
