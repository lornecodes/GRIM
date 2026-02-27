"use client";

import type { ChatMessage } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MetaBadge } from "./MetaBadge";
import { TraceLog } from "./TraceLog";

interface MessageProps {
  message: ChatMessage;
}

function ThinkingIndicator() {
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
      <span className="text-xs text-grim-text-dim">thinking</span>
    </div>
  );
}

export function Message({ message }: MessageProps) {
  const isUser = message.role === "user";
  const isStreaming = message.streaming;
  const isEmpty = !message.content;

  return (
    <div
      className={`animate-fade-in ${isUser ? "self-end" : "self-start"} max-w-[80%]`}
    >
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
  );
}
