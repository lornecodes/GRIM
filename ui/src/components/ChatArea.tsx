"use client";

import { useRef, useEffect } from "react";
import type { ChatMessage } from "@/lib/types";
import { Message } from "./Message";
import { ChatInput } from "./ChatInput";

interface ChatAreaProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  onSend: (text: string) => void;
  connected: boolean;
}

export function ChatArea({
  messages,
  isStreaming,
  onSend,
  connected,
}: ChatAreaProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new messages or streaming updates
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex-1 flex flex-col min-w-0">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-5 flex flex-col gap-4">
        {messages.length === 0 && (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <div className="text-grim-accent text-3xl mb-3 font-bold">G</div>
              <div className="text-sm text-grim-text-dim">
                Ready when you are.
              </div>
              <div className="text-xs text-grim-text-dim mt-1">
                Ask me anything — I have access to the knowledge vault.
              </div>
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <Message key={msg.id} message={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <ChatInput
        onSend={onSend}
        disabled={isStreaming || !connected}
        placeholder={
          !connected
            ? "Connecting..."
            : isStreaming
              ? "GRIM is thinking..."
              : "Talk to GRIM..."
        }
      />
    </div>
  );
}
