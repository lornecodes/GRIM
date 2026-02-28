"use client";

import type { ChatMessage } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MetaBadge } from "./MetaBadge";
import { TraceLog } from "./TraceLog";
import { GrimSprite } from "./GrimSprite";

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

function ThinkingIndicator({ label = "thinking" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="w-1.5 h-1.5 rounded-full bg-grim-accent animate-pulse-dot"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </div>
      <span className="text-xs text-grim-text-dim">{label}</span>
    </div>
  );
}

export function Message({ message }: MessageProps) {
  const isUser = message.role === "user";
  const isStreaming = message.streaming;
  const isEmpty = !message.content;
  const isStep = message.isStep;
  const accentColor = isStep && message.node ? NODE_COLORS[message.node] || "#3e5c72" : undefined;

  // Step bubble: compact, left-accented, no avatar
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
