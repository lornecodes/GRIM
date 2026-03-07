"use client";

import { useRef, useEffect } from "react";
import { useGrimStore } from "@/store";
import { Message } from "./Message";
import { ChatInput } from "./ChatInput";
import type { FileAttachment } from "./ChatInput";
import { GrimSprite } from "./GrimSprite";

interface ChatAreaProps {
  onSend: (text: string, files?: FileAttachment[]) => void;
  onCancel: () => void;
}

export function ChatArea({ onSend, onCancel }: ChatAreaProps) {
  const messages = useGrimStore((s) => s.messages);
  const isStreaming = useGrimStore((s) => s.isStreaming);
  const wsStatus = useGrimStore((s) => s.wsStatus);
  const chatPanelOpen = useGrimStore((s) => s.chatPanelOpen);
  const queuedCount = useGrimStore((s) => s.queuedCount);
  const bottomRef = useRef<HTMLDivElement>(null);

  const connected = wsStatus === "connected";

  // Auto-scroll on new messages (only when panel is visible)
  useEffect(() => {
    if (chatPanelOpen) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, chatPanelOpen]);

  return (
    <div className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-4 flex flex-col gap-3">
        {messages.length === 0 && (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center flex flex-col items-center">
              <GrimSprite size="md" />
              <div className="text-[11px] text-grim-text-dim mt-2">
                GRIM is ready.
              </div>
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <Message key={msg.id} message={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input — always enabled when connected (messages queue while streaming) */}
      <ChatInput
        onSend={onSend}
        onCancel={onCancel}
        isStreaming={isStreaming}
        queuedCount={queuedCount}
        disabled={!connected}
        placeholder={
          !connected
            ? "Connecting..."
            : isStreaming
              ? "Queue a message..."
              : "Talk to GRIM..."
        }
      />
    </div>
  );
}
