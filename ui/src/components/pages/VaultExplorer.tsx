"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconVault } from "@/components/icons/NavIcons";
import { KnowledgeGraph, DOMAIN_COLORS } from "@/components/ui/KnowledgeGraph";
import {
  useVaultExplorer,
  type FDOSummary,
  type FDOFull,
  type TagData,
} from "@/hooks/useVaultExplorer";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DOMAINS = [
  "physics", "ai-systems", "tools", "personal", "modelling", "computing",
  "projects", "people", "interests", "notes", "media", "journal",
];

const STATUSES = ["seed", "developing", "stable", "validated", "archived"];

const STATUS_COLORS: Record<string, string> = {
  seed: "bg-yellow-500/20 text-yellow-400",
  developing: "bg-blue-500/20 text-blue-400",
  stable: "bg-green-500/20 text-green-400",
  validated: "bg-purple-500/20 text-purple-400",
  archived: "bg-gray-500/20 text-gray-400",
};

type Tab = "list" | "graph" | "tags";

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function VaultExplorer() {
  const vault = useVaultExplorer();
  const [activeTab, setActiveTab] = useState<Tab>("list");
  const [searchInput, setSearchInput] = useState("");
  const [showDetail, setShowDetail] = useState(false);
  const [editMode, setEditMode] = useState<"edit" | "create" | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Load graph/tags when switching tabs
  useEffect(() => {
    if (activeTab === "graph" && !vault.graphData && !vault.graphLoading) {
      vault.fetchGraph();
    }
    if (activeTab === "tags" && !vault.tagData) {
      vault.fetchTags();
    }
  }, [activeTab]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = useCallback(() => {
    if (searchInput.trim()) {
      vault.search(searchInput.trim());
    } else {
      vault.refresh();
    }
  }, [searchInput, vault]);

  const handleSelectFdo = useCallback(
    (id: string) => {
      vault.fetchFdo(id);
      setShowDetail(true);
    },
    [vault]
  );

  const handleTagClick = useCallback(
    (tag: string) => {
      setSearchInput(tag);
      setActiveTab("list");
      vault.search(tag);
    },
    [vault]
  );

  const handleSaveEdit = useCallback(
    async (fields: Record<string, unknown>) => {
      if (!vault.selectedFdo) return;
      const result = await vault.updateFdo(vault.selectedFdo.id, fields);
      if (result) {
        setToast("FDO updated");
        vault.fetchFdo(vault.selectedFdo.id);
        vault.refresh();
        setEditMode(null);
        setTimeout(() => setToast(null), 2000);
      }
    },
    [vault]
  );

  const handleCreate = useCallback(
    async (args: Record<string, unknown>) => {
      const result = await vault.createFdo(args);
      if (result) {
        setToast("FDO created");
        vault.refresh();
        setEditMode(null);
        setTimeout(() => setToast(null), 2000);
      }
    },
    [vault]
  );

  const displayed = vault.searchResults ?? vault.fdos;
  const filtered = displayed.filter(
    (f) => !vault.statusFilter || f.status === vault.statusFilter
  );

  return (
    <div className="flex flex-col h-full max-w-[1400px] mx-auto pb-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4 shrink-0">
        <IconVault size={32} className="text-grim-accent" />
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-grim-text">
            Vault Explorer
          </h2>
          <p className="text-xs text-grim-text-dim">
            {vault.fdos.length} FDOs across {DOMAINS.length} domains
          </p>
        </div>
        <button
          onClick={() => setEditMode("create")}
          className="text-[10px] px-3 py-1.5 rounded bg-grim-accent text-white hover:bg-grim-accent-dim transition-colors"
        >
          + New FDO
        </button>
        <button
          onClick={() => { vault.refresh(); vault.fetchGraph(); }}
          className="text-[10px] px-3 py-1.5 rounded border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 mb-4 shrink-0">
        {(["list", "graph", "tags"] as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-1.5 text-[11px] rounded-t transition-colors ${
              activeTab === tab
                ? "bg-grim-surface text-grim-accent border border-grim-border border-b-transparent"
                : "text-grim-text-dim hover:text-grim-text"
            }`}
          >
            {tab === "list" ? "List" : tab === "graph" ? "Graph" : "Tags"}
          </button>
        ))}
      </div>

      {/* Error */}
      {vault.error && (
        <div className="text-xs text-red-400 bg-red-400/10 rounded px-3 py-2 mb-3 shrink-0">
          {vault.error}
        </div>
      )}

      {/* Tab content */}
      <div className="flex-1 min-h-0 bg-grim-surface border border-grim-border rounded-xl overflow-hidden">
        {activeTab === "list" && (
          <ListView
            fdos={filtered}
            loading={vault.loading}
            searchInput={searchInput}
            setSearchInput={setSearchInput}
            onSearch={handleSearch}
            onSelect={handleSelectFdo}
            domainFilter={vault.domainFilter}
            setDomainFilter={vault.setDomainFilter}
            statusFilter={vault.statusFilter}
            setStatusFilter={vault.setStatusFilter}
          />
        )}
        {activeTab === "graph" && (
          <GraphView
            graphData={vault.graphData}
            graphLoading={vault.graphLoading}
            onNodeClick={handleSelectFdo}
            fetchGraph={vault.fetchGraph}
            highlightNodeId={vault.selectedFdo?.id}
          />
        )}
        {activeTab === "tags" && (
          <TagsView tagData={vault.tagData} onTagClick={handleTagClick} />
        )}
      </div>

      {/* Detail panel */}
      {showDetail && vault.selectedFdo && (
        <FDODetailPanel
          fdo={vault.selectedFdo}
          onClose={() => { setShowDetail(false); vault.setSelectedFdo(null); }}
          onEdit={() => setEditMode("edit")}
          onNavigate={handleSelectFdo}
        />
      )}

      {/* Edit/Create modal */}
      {editMode && (
        <FDOFormModal
          fdo={editMode === "edit" ? vault.selectedFdo ?? undefined : undefined}
          mode={editMode}
          saving={vault.saving}
          onSave={editMode === "edit" ? handleSaveEdit : handleCreate}
          onClose={() => setEditMode(null)}
        />
      )}

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 z-50 px-4 py-2 rounded-lg bg-grim-success/20 text-grim-success text-xs animate-fade-in">
          {toast}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ListView
// ---------------------------------------------------------------------------

interface ListViewProps {
  fdos: FDOSummary[];
  loading: boolean;
  searchInput: string;
  setSearchInput: (v: string) => void;
  onSearch: () => void;
  onSelect: (id: string) => void;
  domainFilter: string;
  setDomainFilter: (v: string) => void;
  statusFilter: string;
  setStatusFilter: (v: string) => void;
}

function ListView({
  fdos, loading, searchInput, setSearchInput, onSearch, onSelect,
  domainFilter, setDomainFilter, statusFilter, setStatusFilter,
}: ListViewProps) {
  const [sortKey, setSortKey] = useState<string>("title");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const sorted = [...fdos].sort((a, b) => {
    const aVal = (a as any)[sortKey] ?? "";
    const bVal = (b as any)[sortKey] ?? "";
    const cmp = typeof aVal === "number" ? aVal - bVal : String(aVal).localeCompare(String(bVal));
    return sortDir === "asc" ? cmp : -cmp;
  });

  const toggleSort = (key: string) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Filters */}
      <div className="flex gap-2 p-3 border-b border-grim-border shrink-0">
        <input
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSearch()}
          placeholder="Search FDOs..."
          className="flex-1 px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs placeholder:text-grim-text-dim focus:outline-none focus:border-grim-accent"
        />
        <select
          value={domainFilter}
          onChange={(e) => setDomainFilter(e.target.value)}
          className="px-2 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs"
        >
          <option value="">All domains</option>
          {DOMAINS.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-2 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs"
        >
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      {/* Table header */}
      <div className="grid grid-cols-[1fr_100px_90px_60px_160px] gap-2 px-3 py-2 text-[10px] text-grim-text-dim uppercase border-b border-grim-border/50 shrink-0">
        <SortHeader label="Title" sortKey="title" current={sortKey} dir={sortDir} onSort={toggleSort} />
        <SortHeader label="Domain" sortKey="domain" current={sortKey} dir={sortDir} onSort={toggleSort} />
        <SortHeader label="Status" sortKey="status" current={sortKey} dir={sortDir} onSort={toggleSort} />
        <SortHeader label="Conf" sortKey="confidence" current={sortKey} dir={sortDir} onSort={toggleSort} />
        <span>Tags</span>
      </div>

      {/* Rows */}
      <div className="flex-1 overflow-y-auto">
        {loading && sorted.length === 0 ? (
          <div className="text-xs text-grim-text-dim py-8 text-center">Loading...</div>
        ) : sorted.length === 0 ? (
          <div className="text-xs text-grim-text-dim py-8 text-center">No FDOs found</div>
        ) : (
          sorted.map((fdo) => (
            <div
              key={fdo.id}
              onClick={() => onSelect(fdo.id)}
              className="grid grid-cols-[1fr_100px_90px_60px_160px] gap-2 px-3 py-2 text-[11px] hover:bg-grim-surface-hover cursor-pointer border-b border-grim-border/20 transition-colors"
            >
              <div className="truncate">
                <span className="text-grim-text">{fdo.title}</span>
                <span className="text-grim-text-dim ml-1 text-[9px]">{fdo.id}</span>
              </div>
              <DomainBadge domain={fdo.domain} />
              <StatusBadge status={fdo.status} />
              <ConfidenceBar confidence={fdo.confidence} />
              <div className="flex flex-wrap gap-1 overflow-hidden max-h-5">
                {fdo.tags?.slice(0, 3).map((t) => (
                  <span key={t} className="text-[9px] px-1.5 py-0.5 rounded-full bg-grim-border/30 text-grim-text-dim truncate max-w-[60px]">
                    {t}
                  </span>
                ))}
                {(fdo.tags?.length ?? 0) > 3 && (
                  <span className="text-[9px] text-grim-text-dim">+{fdo.tags!.length - 3}</span>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-1.5 text-[10px] text-grim-text-dim border-t border-grim-border/50 shrink-0">
        {sorted.length} FDOs
      </div>
    </div>
  );
}

function SortHeader({
  label, sortKey, current, dir, onSort,
}: {
  label: string; sortKey: string; current: string; dir: string;
  onSort: (key: string) => void;
}) {
  return (
    <button onClick={() => onSort(sortKey)} className="text-left hover:text-grim-text transition-colors">
      {label} {current === sortKey && (dir === "asc" ? "↑" : "↓")}
    </button>
  );
}

// ---------------------------------------------------------------------------
// GraphView
// ---------------------------------------------------------------------------

interface GraphViewProps {
  graphData: any;
  graphLoading: boolean;
  onNodeClick: (id: string) => void;
  fetchGraph: (scope?: string) => void;
  highlightNodeId?: string | null;
}

function GraphView({
  graphData, graphLoading, onNodeClick, fetchGraph, highlightNodeId,
}: GraphViewProps) {
  const [scope, setScope] = useState("all");
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 800, height: 600 });

  // Track container size
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setDims({ width: Math.round(width), height: Math.round(height) });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Refetch on scope change
  useEffect(() => {
    fetchGraph(scope);
  }, [scope]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={containerRef} className="relative w-full h-full">
      {/* Scope filter overlay */}
      <div className="absolute top-3 right-3 z-10 flex gap-1">
        {["all", "knowledge", "architecture", "tasks"].map((s) => (
          <button
            key={s}
            onClick={() => setScope(s)}
            className={`text-[9px] px-2 py-1 rounded transition-colors ${
              scope === s
                ? "bg-grim-accent text-white"
                : "bg-grim-bg/80 text-grim-text-dim hover:text-grim-text border border-grim-border/50"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Domain legend */}
      <div className="absolute bottom-3 left-3 z-10 flex flex-wrap gap-x-3 gap-y-1 bg-grim-bg/70 rounded px-2 py-1.5">
        {Object.entries(DOMAIN_COLORS).map(([domain, color]) => (
          <span key={domain} className="flex items-center gap-1 text-[9px] text-grim-text-dim">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: color }} />
            {domain}
          </span>
        ))}
      </div>

      {/* Node count */}
      {graphData && (
        <div className="absolute top-3 left-3 z-10 text-[10px] text-grim-text-dim bg-grim-bg/70 rounded px-2 py-1">
          {graphData.count} nodes · {graphData.edges.length} edges
        </div>
      )}

      {/* Graph */}
      {graphLoading ? (
        <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
          Loading graph...
        </div>
      ) : graphData ? (
        <KnowledgeGraph
          data={graphData}
          width={dims.width}
          height={dims.height}
          onNodeClick={onNodeClick}
          highlightNodeId={highlightNodeId}
        />
      ) : (
        <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
          No graph data
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TagsView
// ---------------------------------------------------------------------------

function TagsView({
  tagData,
  onTagClick,
}: {
  tagData: TagData | null;
  onTagClick: (tag: string) => void;
}) {
  if (!tagData) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-grim-text-dim">
        Loading tags...
      </div>
    );
  }

  return (
    <div className="p-4 overflow-y-auto h-full space-y-6">
      {/* Top tags cloud */}
      <div>
        <h3 className="text-xs font-semibold text-grim-text mb-3">
          Top Tags ({tagData.total_tags} total across {tagData.total_fdos} FDOs)
        </h3>
        <div className="flex flex-wrap gap-2">
          {tagData.top_tags.slice(0, 40).map((t) => (
            <button
              key={t.tag}
              onClick={() => onTagClick(t.tag)}
              className="px-2.5 py-1 rounded-full bg-grim-accent/10 text-grim-accent hover:bg-grim-accent/20 transition-colors"
              style={{ fontSize: `${Math.max(10, Math.min(15, 9 + Math.sqrt(t.count)))}px` }}
            >
              {t.tag}
              <span className="text-grim-text-dim ml-1 text-[9px]">{t.count}</span>
            </button>
          ))}
        </div>
      </div>

      {/* By domain */}
      {Object.entries(tagData.by_domain)
        .sort(([, a], [, b]) => b.length - a.length)
        .map(([domain, tags]) => (
          <div key={domain}>
            <div className="flex items-center gap-2 mb-2">
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ backgroundColor: DOMAIN_COLORS[domain] || "#8888a0" }}
              />
              <h4 className="text-[11px] font-semibold text-grim-text">{domain}</h4>
              <span className="text-[9px] text-grim-text-dim">{tags.length} tags</span>
            </div>
            <div className="flex flex-wrap gap-1.5 ml-4">
              {tags.slice(0, 20).map((t) => (
                <button
                  key={t.tag}
                  onClick={() => onTagClick(t.tag)}
                  className="text-[10px] px-2 py-0.5 rounded-full bg-grim-border/30 text-grim-text-dim hover:text-grim-text hover:bg-grim-border/50 transition-colors"
                >
                  {t.tag} <span className="opacity-60">{t.count}</span>
                </button>
              ))}
              {tags.length > 20 && (
                <span className="text-[9px] text-grim-text-dim self-center">+{tags.length - 20} more</span>
              )}
            </div>
          </div>
        ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FDO Detail Panel (slide-out)
// ---------------------------------------------------------------------------

function FDODetailPanel({
  fdo, onClose, onEdit, onNavigate,
}: {
  fdo: FDOFull;
  onClose: () => void;
  onEdit: () => void;
  onNavigate: (id: string) => void;
}) {
  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-30 bg-black/40" onClick={onClose} />

      {/* Panel */}
      <div className="fixed inset-y-0 right-0 w-[520px] max-w-[90vw] z-40 bg-grim-surface border-l border-grim-border shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="shrink-0 border-b border-grim-border p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <h2 className="text-sm font-semibold text-grim-text truncate">{fdo.title}</h2>
              <span className="text-[10px] text-grim-text-dim font-mono">{fdo.id}</span>
            </div>
            <div className="flex gap-1.5 shrink-0">
              <button
                onClick={onEdit}
                className="text-[10px] px-2.5 py-1 rounded bg-grim-accent/15 text-grim-accent hover:bg-grim-accent/25 transition-colors"
              >
                Edit
              </button>
              <button
                onClick={onClose}
                className="text-[10px] px-2 py-1 rounded text-grim-text-dim hover:text-grim-text transition-colors"
              >
                ✕
              </button>
            </div>
          </div>

          {/* Metadata */}
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <DomainBadge domain={fdo.domain} />
            <StatusBadge status={fdo.status} />
            <ConfidenceBar confidence={fdo.confidence} showLabel />
            {fdo.created && (
              <span className="text-[9px] text-grim-text-dim">created {fdo.created}</span>
            )}
            {fdo.updated && (
              <span className="text-[9px] text-grim-text-dim">updated {fdo.updated}</span>
            )}
          </div>

          {/* Tags */}
          {fdo.tags && fdo.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {fdo.tags.map((t) => (
                <span key={t} className="text-[9px] px-1.5 py-0.5 rounded-full bg-grim-accent/10 text-grim-accent">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="prose prose-invert prose-sm max-w-none prose-headings:text-grim-text prose-p:text-grim-text-dim prose-a:text-grim-accent prose-code:text-grim-accent prose-strong:text-grim-text">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {fdo.body || "_No content_"}
            </ReactMarkdown>
          </div>

          {/* Related */}
          {fdo.related && fdo.related.length > 0 && (
            <div className="mt-6 pt-4 border-t border-grim-border">
              <h4 className="text-[10px] text-grim-text-dim uppercase mb-2">
                Related ({fdo.related.length})
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {fdo.related.map((id) => (
                  <button
                    key={id}
                    onClick={() => onNavigate(id)}
                    className="text-[10px] px-2 py-0.5 rounded bg-grim-bg text-grim-accent hover:bg-grim-surface-hover transition-colors font-mono"
                  >
                    {id}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Source paths */}
          {fdo.source_paths && fdo.source_paths.length > 0 && (
            <div className="mt-4 pt-4 border-t border-grim-border">
              <h4 className="text-[10px] text-grim-text-dim uppercase mb-2">
                Source Paths ({fdo.source_paths.length})
              </h4>
              <div className="space-y-1">
                {fdo.source_paths.map((sp, i) => (
                  <div key={i} className="text-[10px] font-mono text-grim-text-dim">
                    <span className="text-grim-accent">{sp.repo}</span>/{sp.path}
                    <span className="text-grim-text-dim ml-1">({sp.type})</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Log */}
          {fdo.log && fdo.log.length > 0 && (
            <LogSection entries={fdo.log} />
          )}
        </div>
      </div>
    </>
  );
}

function LogSection({ entries }: { entries: string[] }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-4 pt-4 border-t border-grim-border">
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-[10px] text-grim-text-dim uppercase hover:text-grim-text transition-colors"
      >
        Log ({entries.length}) {expanded ? "▾" : "▸"}
      </button>
      {expanded && (
        <div className="mt-2 space-y-1.5 max-h-60 overflow-y-auto">
          {entries.map((entry, i) => (
            <div key={i} className="text-[10px] text-grim-text-dim leading-relaxed">
              {entry}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FDO Form Modal (Edit / Create)
// ---------------------------------------------------------------------------

function FDOFormModal({
  fdo, mode, saving, onSave, onClose,
}: {
  fdo?: FDOFull;
  mode: "edit" | "create";
  saving: boolean;
  onSave: (fields: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [id, setId] = useState(fdo?.id || "");
  const [title, setTitle] = useState(fdo?.title || "");
  const [domain, setDomain] = useState(fdo?.domain || "ai-systems");
  const [status, setStatus] = useState(fdo?.status || "seed");
  const [confidence, setConfidence] = useState(fdo?.confidence ?? 0.5);
  const [tags, setTags] = useState(fdo?.tags?.join(", ") || "");
  const [related, setRelated] = useState(fdo?.related?.join(", ") || "");
  const [body, setBody] = useState(
    fdo?.body || "# Title\n\n## Summary\n\n## Details\n\n## Connections\n"
  );
  const [confidenceBasis, setConfidenceBasis] = useState(fdo?.confidence_basis || "");

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleSave = () => {
    const parseTags = (s: string) => s.split(",").map((t) => t.trim()).filter(Boolean);

    if (mode === "create") {
      onSave({
        id: id.trim(),
        title: title.trim(),
        domain,
        status,
        confidence,
        body,
        tags: parseTags(tags),
        related: parseTags(related),
        ...(confidenceBasis ? { confidence_basis: confidenceBasis } : {}),
      });
    } else {
      // Build diff
      const fields: Record<string, unknown> = {};
      if (title !== fdo?.title) fields.title = title;
      if (domain !== fdo?.domain) fields.domain = domain;
      if (status !== fdo?.status) fields.status = status;
      if (confidence !== fdo?.confidence) fields.confidence = confidence;
      if (body !== fdo?.body) fields.body = body;
      if (tags !== fdo?.tags?.join(", ")) fields.tags = parseTags(tags);
      if (related !== fdo?.related?.join(", ")) fields.related = parseTags(related);
      if (confidenceBasis !== (fdo?.confidence_basis || ""))
        fields.confidence_basis = confidenceBasis;
      if (Object.keys(fields).length > 0) onSave(fields);
      else onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-grim-surface border border-grim-border rounded-xl p-5 max-w-2xl w-full mx-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-grim-text mb-4">
          {mode === "create" ? "Create New FDO" : `Edit: ${fdo?.id}`}
        </h3>

        <div className="space-y-3">
          {/* ID (create only) */}
          {mode === "create" && (
            <FormField label="ID (kebab-case)">
              <input
                value={id}
                onChange={(e) => setId(e.target.value)}
                placeholder="my-new-fdo"
                className="w-full px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs font-mono"
              />
            </FormField>
          )}

          {/* Title */}
          <FormField label="Title">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs"
            />
          </FormField>

          {/* Domain + Status row */}
          <div className="grid grid-cols-2 gap-3">
            <FormField label="Domain">
              <select
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                className="w-full px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs"
              >
                {DOMAINS.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>
            </FormField>
            <FormField label="Status">
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="w-full px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs"
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </FormField>
          </div>

          {/* Confidence */}
          <FormField label={`Confidence: ${confidence.toFixed(1)}`}>
            <input
              type="range"
              min={0} max={1} step={0.1}
              value={confidence}
              onChange={(e) => setConfidence(parseFloat(e.target.value))}
              className="w-full accent-grim-accent"
            />
          </FormField>

          {/* Confidence basis */}
          <FormField label="Confidence Basis">
            <input
              value={confidenceBasis}
              onChange={(e) => setConfidenceBasis(e.target.value)}
              placeholder="Why this confidence level?"
              className="w-full px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs"
            />
          </FormField>

          {/* Tags */}
          <FormField label="Tags (comma-separated)">
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="tag1, tag2, tag3"
              className="w-full px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs"
            />
          </FormField>

          {/* Related */}
          <FormField label="Related FDO IDs (comma-separated)">
            <input
              value={related}
              onChange={(e) => setRelated(e.target.value)}
              placeholder="fdo-id-1, fdo-id-2"
              className="w-full px-3 py-1.5 rounded bg-grim-bg border border-grim-border text-grim-text text-xs font-mono"
            />
          </FormField>

          {/* Body */}
          <FormField label="Body (Markdown)">
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={16}
              className="w-full px-3 py-2 rounded bg-grim-bg border border-grim-border text-grim-text text-xs font-mono leading-relaxed resize-y"
            />
          </FormField>
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-2 mt-4 pt-3 border-t border-grim-border">
          <button
            onClick={onClose}
            className="text-[10px] px-4 py-1.5 rounded border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || (mode === "create" && (!id.trim() || !title.trim()))}
            className="text-[10px] px-4 py-1.5 rounded bg-grim-accent text-white hover:bg-grim-accent-dim transition-colors disabled:opacity-40"
          >
            {saving ? "Saving..." : mode === "create" ? "Create" : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-[10px] text-grim-text-dim uppercase block mb-1">{label}</label>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared Sub-components
// ---------------------------------------------------------------------------

function DomainBadge({ domain }: { domain: string }) {
  const color = DOMAIN_COLORS[domain] || "#8888a0";
  return (
    <span className="inline-flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: `${color}20`, color }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: color }} />
      {domain}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${STATUS_COLORS[status] || "bg-gray-500/20 text-gray-400"}`}>
      {status}
    </span>
  );
}

function ConfidenceBar({ confidence, showLabel }: { confidence: number; showLabel?: boolean }) {
  const pct = Math.round(confidence * 100);
  const color = pct >= 80 ? "#4ade80" : pct >= 50 ? "#fbbf24" : "#f87171";
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-10 h-1.5 rounded-full bg-grim-border/50 overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      {showLabel && <span className="text-[9px] text-grim-text-dim">{pct}%</span>}
    </div>
  );
}
