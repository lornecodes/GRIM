"use client";

import { IconSkills } from "@/components/icons/NavIcons";
import { useSkills, type SkillData } from "@/hooks/useSkills";

// ---------------------------------------------------------------------------
// Toggle switch
// ---------------------------------------------------------------------------

function Toggle({
  enabled,
  loading,
  onToggle,
}: {
  enabled: boolean;
  loading: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      disabled={loading}
      className={`relative w-9 h-5 rounded-full transition-colors ${
        enabled ? "bg-grim-accent" : "bg-grim-border"
      } ${loading ? "opacity-50" : "cursor-pointer"}`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
          enabled ? "translate-x-4" : ""
        }`}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Skill card
// ---------------------------------------------------------------------------

function SkillCard({
  skill,
  toggling,
  onToggle,
}: {
  skill: SkillData;
  toggling: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className={`bg-grim-surface border rounded-xl p-4 transition-all ${
        skill.enabled
          ? "border-grim-border hover:border-grim-accent/40"
          : "border-grim-border/50 opacity-60"
      }`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-grim-text truncate">
              {skill.name}
            </h3>
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim font-mono">
              v{skill.version}
            </span>
          </div>
        </div>
        <Toggle enabled={skill.enabled} loading={toggling} onToggle={onToggle} />
      </div>

      {/* Description */}
      <p className="text-[11px] text-grim-text-dim leading-relaxed mb-3 line-clamp-2">
        {skill.description || "No description"}
      </p>

      {/* Tags */}
      <div className="flex flex-wrap gap-1">
        {skill.type && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-accent/10 text-grim-accent">
            {skill.type}
          </span>
        )}
        {skill.phases.length > 0 && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim">
            {skill.phases.length} phases
          </span>
        )}
        {skill.permissions.map((p) => (
          <span
            key={p}
            className="text-[9px] px-1.5 py-0.5 rounded bg-grim-border/30 text-grim-text-dim"
          >
            {p}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skills Explorer page
// ---------------------------------------------------------------------------

export function SkillsExplorer() {
  const { skills, loading, error, toggling, toggleSkill, refresh } = useSkills();

  const enabledCount = skills.filter((s) => s.enabled).length;

  return (
    <div className="max-w-6xl mx-auto space-y-6 pb-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <IconSkills size={32} className="text-grim-accent" />
        <div className="flex-1">
          <h2 className="text-lg font-semibold text-grim-text">
            Skills Explorer
          </h2>
          <p className="text-xs text-grim-text-dim">
            {enabledCount} enabled / {skills.length} total
          </p>
        </div>
        <button
          onClick={refresh}
          className="text-[10px] px-2 py-1 rounded bg-grim-surface border border-grim-border text-grim-text-dim hover:text-grim-text transition-colors"
        >
          Refresh
        </button>
      </div>

      {loading && (
        <div className="text-xs text-grim-text-dim py-8 text-center">
          Loading skills...
        </div>
      )}

      {error && (
        <div className="text-xs text-red-400 py-8 text-center">{error}</div>
      )}

      {!loading && !error && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {skills.map((skill) => (
            <SkillCard
              key={skill.name}
              skill={skill}
              toggling={toggling === skill.name}
              onToggle={() => toggleSkill(skill.name)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
