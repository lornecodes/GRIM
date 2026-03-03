"use client";

import { useState } from "react";
import type { ChatMessage } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MetaBadge } from "./MetaBadge";
import { TraceLog } from "./TraceLog";
import { GrimSprite } from "./GrimSprite";
import { AgentLogBlock } from "./AgentLogBlock";
import { GrimTypingSprite } from "./GrimTypingSprite";

interface MessageProps {
  message: ChatMessage;
}

const NODE_COLORS: Record<string, string> = {
  companion: "#7c6fef",
  dispatch: "#34d399",
  integrate: "#3e5c72",
  router: "#f59e0b",
  identity: "#6b7280",
  memory: "#8b5cf6",
  skill_match: "#ec4899",
  evolve: "#06b6d4",
};

/** Nodes whose step bubbles are collapsed by default (background system work). */
const COLLAPSED_NODES = new Set(["evolve", "memory", "identity", "compress"]);

/** Short labels for collapsed system step bubbles. */
const COLLAPSED_LABELS: Record<string, string> = {
  evolve: "memory synced",
  memory: "knowledge loaded",
  identity: "identity loaded",
  compress: "context compressed",
};

function ThinkingIndicator({ label = "thinking" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <GrimTypingSprite size="xs" />
      <span className="text-xs text-grim-text-dim">{label}</span>
    </div>
  );
}

/**
 * Collapsible system step bubble — shows a one-line summary with expand toggle.
 * Used for evolve, memory, identity, compress nodes so they don't overwhelm the chat.
 */
function CollapsibleStep({
  message,
  accentColor,
}: {
  message: ChatMessage;
  accentColor: string | undefined;
}) {
  const [expanded, setExpanded] = useState(false);
  const label = COLLAPSED_LABELS[message.node || ""] || message.node;
  const hasContent = !!message.content;

  return (
    <div className="animate-fade-in self-start pl-[42px] max-w-[85%]">
      <button
        onClick={() => hasContent && setExpanded(!expanded)}
        className="flex items-center gap-2 px-3 py-1 rounded text-[11px] bg-grim-grim-bg/40 border border-grim-border/30 hover:border-grim-border/60 transition-colors w-full text-left"
        style={{ borderLeftWidth: 2, borderLeftColor: accentColor }}
      >
        <span
          className="uppercase tracking-wider font-medium"
          style={{ color: accentColor }}
        >
          {message.node}
        </span>
        <span className="text-grim-text-dim">{label}</span>
        {message.streaming && (
          <div className="ml-1">
            <GrimTypingSprite size="xs" />
          </div>
        )}
        {hasContent && !message.streaming && (
          <span className="text-grim-text-dim ml-auto text-[10px]">
            {expanded ? "▾" : "▸"}
          </span>
        )}
      </button>
      {expanded && hasContent && (
        <div
          className="px-3 py-2 mt-0.5 rounded text-[11px] leading-relaxed bg-grim-grim-bg/30 border border-grim-border/20 max-h-[200px] overflow-y-auto"
          style={{ borderLeftWidth: 2, borderLeftColor: accentColor }}
        >
          <div className="grim-prose text-grim-text-dim">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

export function Message({ message }: MessageProps) {
  const isUser = message.role === "user";
  const isStreaming = message.streaming;
  const isEmpty = !message.content;
  const isStep = message.isStep;
  const accentColor = isStep && message.node ? NODE_COLORS[message.node] || "#3e5c72" : undefined;

  // Agent log block for dispatch/integrate — terminal-style output
  if (isStep && (message.node === "dispatch" || message.node === "integrate")) {
    return (
      <AgentLogBlock
        content={message.content}
        traces={message.traces}
        streaming={isStreaming}
        node={message.node}
      />
    );
  }

  // Collapsible system steps — show one-line summary, expand on click
  if (isStep && message.node && COLLAPSED_NODES.has(message.node)) {
    return <CollapsibleStep message={message} accentColor={accentColor} />;
  }

  // Step bubble: compact, left-accented, no avatar (other nodes)
  if (isStep) {
    return (
      <div className="animate-fade-in self-start pl-[42px] max-w-[85%]">
        <div
          className="px-3 py-2 rounded-lg text-[12.5px] leading-relaxed bg-grim-grim-bg/60 border border-grim-border/50 rounded-bl-sm"
          style={{ borderLeftWidth: 3, borderLeftColor: accentColor }}
        >
          <div className="text-[10px] text-grim-text-dim mb-1 uppercase tracking-wider font-medium" style={{ color: accentColor }}>
            {message.node}
          </div>
          {isEmpty && isStreaming ? (
            <ThinkingIndicator />
          ) : (
            <div className="grim-prose">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`animate-fade-in ${isUser ? "self-end" : "self-start flex gap-2"} max-w-[85%]`}
    >
      {/* GRIM avatar */}
      {!isUser && (
        <div className="shrink-0 mt-1">
          <GrimSprite size="sm" />
        </div>
      )}
      <div className="min-w-0 flex-1">
        {/* Message bubble */}
        <div
          className={`px-4 py-3 rounded-xl text-[13.5px] leading-relaxed ${
            isUser
              ? "bg-grim-user-bg border border-grim-border rounded-br-sm"
              : message.error
                ? "bg-grim-error/10 border border-grim-error/30 rounded-bl-sm"
                : "bg-grim-grim-bg border border-grim-border rounded-bl-sm"
          }`}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap">{message.content}</div>
          ) : isEmpty && isStreaming && message.thinkingText ? (
            <ThinkingIndicator label="gathering knowledge" />
          ) : isEmpty && isStreaming ? (
            <ThinkingIndicator />
          ) : (
            <div className="grim-prose">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}

          {/* Meta badge */}
          {message.meta && <MetaBadge meta={message.meta} />}
        </div>

        {/* Inline trace log */}
        {!isUser && message.traces.length > 0 && (
          <TraceLog
            traces={message.traces}
            defaultExpanded={isStreaming || false}
          />
        )}
      </div>
    </div>
  );
}
