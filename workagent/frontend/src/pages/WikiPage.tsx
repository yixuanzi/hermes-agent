import { useCallback, useEffect, useRef, useState } from "react";
import { Code2, Eye, Save } from "lucide-react";
import Markdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import remarkGfm from "remark-gfm";

import { fetchDocument, fetchTree, saveDocument, type DocumentResponse, type TreeItem } from "../lib/kb";
import { ApiError } from "../lib/api";
import remarkObsidian from "../lib/remark-obsidian";

type DirChildren = Record<string, TreeItem[]>;
type WikiMode = "browse" | "code";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function describeApiError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  try {
    const payload = JSON.parse(error.message) as { detail?: unknown };
    if (typeof payload.detail === "string" && payload.detail) return payload.detail;
  } catch {
    // Keep the stable fallback for non-JSON responses.
  }
  return error.message || fallback;
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
  const [mode, setMode] = useState<WikiMode>("browse");
  const [draftContent, setDraftContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [saveLoading, setSaveLoading] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveSuccess, setSaveSuccess] = useState("");
  const documentRequestIdRef = useRef(0);

  const hasUnsavedChanges = draftContent !== savedContent;

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
    if (saveLoading) return;
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
    if (filePath === selectedPath || saveLoading) return;
    if (
      hasUnsavedChanges &&
      !window.confirm("You have unsaved edits. Discard changes and switch files?")
    ) {
      return;
    }

    const requestId = ++documentRequestIdRef.current;
    setSelectedPath(filePath);
    setDocument(null);
    setDocLoading(true);
    setDocError("");
    setDraftContent("");
    setSavedContent("");
    setSaveError("");
    setSaveSuccess("");
    try {
      const doc = await fetchDocument(filePath);
      if (requestId !== documentRequestIdRef.current) return;
      setDocument(doc);
      setDraftContent(doc.content);
      setSavedContent(doc.content);
    } catch (error) {
      if (requestId !== documentRequestIdRef.current) return;
      setDocument(null);
      setDraftContent("");
      setSavedContent("");
      setDocError(describeApiError(error, "Failed to load document."));
    } finally {
      if (requestId === documentRequestIdRef.current) setDocLoading(false);
    }
  }

  async function saveCurrentDocument() {
    if (!document || !selectedPath || !hasUnsavedChanges || saveLoading) return;
    setSaveLoading(true);
    setSaveError("");
    setSaveSuccess("");
    try {
      const result = await saveDocument(selectedPath, draftContent);
      setSavedContent(draftContent);
      setDocument((current) =>
        current
          ? {
              ...current,
              content: draftContent,
              size: result.size,
              modified: result.modified,
            }
          : current,
      );
      setSaveSuccess("Document saved.");
    } catch (error) {
      setSaveError(describeApiError(error, "Failed to save document."));
    } finally {
      setSaveLoading(false);
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
              disabled={saveLoading}
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
            disabled={saveLoading}
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
            <div className="wiki-detail-head-title">
              <h3>{document?.name || "Document"}</h3>
              {document && mode === "code" ? (
                <button
                  type="button"
                  className="wiki-save-button wiki-header-save-button"
                  onClick={() => void saveCurrentDocument()}
                  disabled={saveLoading || !hasUnsavedChanges}
                >
                  <Save size={14} strokeWidth={1.8} aria-hidden="true" />
                  {saveLoading ? "Saving..." : "Save"}
                </button>
              ) : null}
            </div>
            <div className="wiki-detail-head-actions">
              <div className="wiki-mode-toggle" role="group" aria-label="Document view mode">
                <button
                  type="button"
                  className={`wiki-mode-button${mode === "browse" ? " active" : ""}`}
                  aria-label="Browse mode"
                  aria-pressed={mode === "browse"}
                  title="Browse mode"
                  disabled={!document || docLoading || saveLoading}
                  onClick={() => setMode("browse")}
                >
                  <Eye size={15} strokeWidth={1.8} aria-hidden="true" />
                  <span>Browse</span>
                </button>
                <button
                  type="button"
                  className={`wiki-mode-button${mode === "code" ? " active" : ""}`}
                  aria-label="Code mode"
                  aria-pressed={mode === "code"}
                  title="Code mode"
                  disabled={!document || docLoading || saveLoading}
                  onClick={() => setMode("code")}
                >
                  <Code2 size={15} strokeWidth={1.8} aria-hidden="true" />
                  <span>Code</span>
                </button>
              </div>
              {document ? <span className="status-badge">{formatFileSize(document.size)}</span> : null}
            </div>
          </div>
          {!selectedPath ? (
            <p className="subtle-copy">Select a file from the tree to view its content.</p>
          ) : null}
          {docLoading ? <p>Loading document…</p> : null}
          {docError ? <p className="error-text">{docError}</p> : null}
          {document && !docLoading ? (
            mode === "code" ? (
              <div className="wiki-editor-shell">
                <label className="wiki-editor-label" htmlFor="wiki-document-editor">
                  Markdown source
                </label>
                <textarea
                  id="wiki-document-editor"
                  className="wiki-document-editor"
                  value={draftContent}
                  disabled={saveLoading}
                  onChange={(event) => {
                    setDraftContent(event.target.value);
                    setSaveError("");
                    setSaveSuccess("");
                  }}
                  spellCheck={false}
                />
                <div className="wiki-editor-footer">
                  <span className={hasUnsavedChanges ? "wiki-dirty-status" : "wiki-saved-status"}>
                    {hasUnsavedChanges ? "Unsaved changes" : "Saved"}
                  </span>
                </div>
                {saveError ? <p className="error-text">{saveError}</p> : null}
                {saveSuccess ? <p className="wiki-save-success">{saveSuccess}</p> : null}
              </div>
            ) : (
              <div className="wiki-preview-shell">
                {hasUnsavedChanges ? <div className="wiki-preview-status">Previewing unsaved changes</div> : null}
                <div className="wiki-detail-content">
                  <Markdown
                    remarkPlugins={[remarkGfm, remarkObsidian]}
                    rehypePlugins={[rehypeRaw]}
                  >
                    {draftContent}
                  </Markdown>
                </div>
              </div>
            )
          ) : null}
        </aside>
      </div>
    </section>
  );
}
