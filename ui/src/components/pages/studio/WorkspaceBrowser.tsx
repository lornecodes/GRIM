"use client";

import { useState, useCallback } from "react";

interface Props {
  workspaceId: string | null;
}

interface FileContent {
  path: string;
  content: string;
  size: number;
}

export function WorkspaceBrowser({ workspaceId }: Props) {
  const [files, setFiles] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [filesLoaded, setFilesLoaded] = useState(false);

  const apiBase = process.env.NEXT_PUBLIC_GRIM_API || "";

  const fetchFiles = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      const resp = await fetch(`${apiBase}/api/pool/workspaces/${workspaceId}/files`);
      if (resp.ok) {
        const data = await resp.json();
        setFiles(data.files ?? []);
        setFilesLoaded(true);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [apiBase, workspaceId]);

  const fetchFile = useCallback(async (path: string) => {
    if (!workspaceId) return;
    setSelectedFile(path);
    setLoading(true);
    try {
      const resp = await fetch(
        `${apiBase}/api/pool/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
      );
      if (resp.ok) {
        setFileContent(await resp.json());
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [apiBase, workspaceId]);

  if (!workspaceId) {
    return (
      <div className="text-[11px] text-grim-text-dim text-center py-8">
        No workspace associated with this job
      </div>
    );
  }

  if (!filesLoaded) {
    return (
      <div className="text-center py-8">
        <button
          onClick={fetchFiles}
          className="text-[11px] px-3 py-1.5 rounded border border-grim-accent bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors"
        >
          Load Workspace Files
        </button>
      </div>
    );
  }

  return (
    <div className="flex gap-2 h-[400px]">
      {/* File list */}
      <div className="w-[250px] shrink-0 border border-grim-border rounded-md overflow-y-auto">
        <div className="text-[10px] text-grim-text-dim uppercase tracking-wider px-2 py-1.5 border-b border-grim-border bg-grim-surface">
          Changed Files ({files.length})
        </div>
        {files.map((f) => (
          <button
            key={f}
            onClick={() => fetchFile(f)}
            className={`w-full text-left px-2 py-1 text-[10px] font-mono truncate transition-colors ${
              selectedFile === f
                ? "bg-grim-accent/10 text-grim-accent"
                : "text-grim-text-dim hover:text-grim-text hover:bg-grim-surface-hover"
            }`}
          >
            {f}
          </button>
        ))}
        {files.length === 0 && (
          <div className="text-[10px] text-grim-text-dim text-center py-4">No files changed</div>
        )}
      </div>

      {/* File content */}
      <div className="flex-1 border border-grim-border rounded-md overflow-auto bg-grim-bg">
        {loading && (
          <div className="text-[11px] text-grim-text-dim text-center py-8">Loading...</div>
        )}
        {!loading && !fileContent && (
          <div className="text-[11px] text-grim-text-dim text-center py-8">
            Select a file to view
          </div>
        )}
        {!loading && fileContent && (
          <div>
            <div className="text-[10px] font-mono text-grim-accent px-3 py-1.5 border-b border-grim-border bg-grim-surface">
              {fileContent.path}
              <span className="text-grim-text-dim ml-2">
                ({(fileContent.size / 1024).toFixed(1)} KB)
              </span>
            </div>
            <pre className="text-[10px] text-grim-text p-3 font-mono leading-[18px] whitespace-pre-wrap">
              {fileContent.content}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
