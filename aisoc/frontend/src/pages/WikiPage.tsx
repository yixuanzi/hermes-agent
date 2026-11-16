import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";

import { fetchDocument, fetchTree, type DocumentResponse, type TreeItem } from "../lib/kb";
import remarkObsidian from "../lib/remark-obsidian";

type DirChildren = Record<string, TreeItem[]>;

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function WikiPage() {
  const [treeItems, setTreeItems] = useState<TreeItem[]>([]);
  const [currentCwd, setCurrentCwd] = useState("");
  const [treeLoading, setTreeLoading] = useState(true);
  const [treeError, setTreeError] = useState("");
  const [expandedDirs, setExpandedDirs] = useState<DirChildren>({});
  const [loadingDirs, setLoadingDirs] = useState<Record<string, boolean>>({});

  const [selectedPath, setSelectedPath] = useState("");
  const [document, setDocument] = useState<DocumentResponse | null>(null);
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState("");

  const loadTree = useCallback(async (cwd: string) => {
    setTreeLoading(true);
    setTreeError("");
    try {
      const res = await fetchTree(cwd || undefined);
      setTreeItems(res.items);
      setCurrentCwd(res.cwd);
    } catch {
      setTreeError("Failed to load directory tree.");
    } finally {
      setTreeLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTree("");
  }, [loadTree]);

  async function toggleDir(dirPath: string) {
    if (expandedDirs[dirPath]) {
      setExpandedDirs((prev) => {
        const next = { ...prev };
        delete next[dirPath];
        return next;
      });
      return;
    }
    setLoadingDirs((prev) => ({ ...prev, [dirPath]: true }));
    try {
      const res = await fetchTree(dirPath);
      setExpandedDirs((prev) => ({ ...prev, [dirPath]: res.items }));
    } catch {
      // ignore — user can retry
    } finally {
      setLoadingDirs((prev) => ({ ...prev, [dirPath]: false }));
    }
  }

  async function selectFile(filePath: string) {
    setSelectedPath(filePath);
    setDocLoading(true);
    setDocError("");
    try {
      const doc = await fetchDocument(filePath);
      setDocument(doc);
    } catch {
      setDocument(null);
      setDocError("Failed to load document.");
    } finally {
      setDocLoading(false);
    }
  }

  const fileCount = treeItems.filter((i) => i.type === "file").length;
  const dirCount = treeItems.filter((i) => i.type === "dir").length;

  function renderTreeItems(items: TreeItem[], depth: number): React.ReactNode {
    return items.map((item, idx) => {
      const isLast = idx === items.length - 1;
      if (item.type === "dir") {
        const isExpanded = Boolean(expandedDirs[item.path]);
        const isLoading = Boolean(loadingDirs[item.path]);
        const children = expandedDirs[item.path] ?? [];
        return (
          <div key={item.path} className="wiki-tree-node">
            <button
              type="button"
              className="wiki-tree-row wiki-tree-dir"
              onClick={() => void toggleDir(item.path)}
            >
              <span className="wiki-tree-indent" aria-hidden="true">
                {"│  ".repeat(depth)}{isLast ? "└─" : "├─"}
              </span>
              <span className="wiki-tree-arrow">{isExpanded ? "▾ " : "▸ "}</span>
              <span className="wiki-tree-name">{item.name}</span>
              {isLoading && <span className="wiki-tree-spinner">…</span>}
            </button>
            {isExpanded && children.length > 0 ? (
              <div className="wiki-tree-children">
                {renderTreeItems(children, depth + 1)}
              </div>
            ) : null}
            {isExpanded && children.length === 0 && !isLoading ? (
              <div className="wiki-tree-row wiki-tree-empty">
                <span className="wiki-tree-indent" aria-hidden="true">
                  {"│  ".repeat(depth + 1)}└─
                </span>
                (empty)
              </div>
            ) : null}
          </div>
        );
      }
      const isActive = selectedPath === item.path;
      return (
        <div key={item.path} className="wiki-tree-node">
          <button
            type="button"
            className={`wiki-tree-row wiki-tree-file${isActive ? " active" : ""}`}
            onClick={() => void selectFile(item.path)}
          >
            <span className="wiki-tree-indent" aria-hidden="true">
              {"│  ".repeat(depth)}{isLast ? "└─" : "├─"}
            </span>
            <span className="wiki-tree-name">{item.name}</span>
            <span className="wiki-tree-size">{formatFileSize(item.size)}</span>
          </button>
        </div>
      );
    });
  }

  return (
    <section className="wiki-workbench-page">
      <div className="wiki-workbench">
        <article className="detail-panel wiki-tree-pane">
          <div className="wiki-tree-head">
            <h3>LLMWiki</h3>
            <span className="status-badge">
              {treeLoading ? "Loading..." : `${dirCount} dirs / ${fileCount} files`}
            </span>
          </div>

          {treeError ? <p className="error-text">{treeError}</p> : null}
          <div className="wiki-tree-scroll">
            {treeItems.length === 0 && !treeLoading ? (
              <p className="subtle-copy">No files found.</p>
            ) : null}
            {renderTreeItems(treeItems, 0)}
          </div>
        </article>

        <aside className="detail-panel wiki-detail-pane">
          <div className="wiki-detail-head">
            <h3>{document?.name || "Document"}</h3>
            {document ? (
              <span className="status-badge">
                {formatFileSize(document.size)}
              </span>
            ) : null}
          </div>
          {!selectedPath ? (
            <p className="subtle-copy">Select a file from the tree to view its content.</p>
          ) : null}
          {docLoading ? <p className="subtle-copy">Loading document…</p> : null}
          {docError ? <p className="error-text">{docError}</p> : null}
          {document && !docLoading ? (
            <div className="wiki-detail-content">
              <Markdown
                remarkPlugins={[remarkGfm, remarkObsidian]}
                rehypePlugins={[rehypeRaw]}
              >
                {document.content}
              </Markdown>
            </div>
          ) : null}
        </aside>
      </div>
    </section>
  );
}
