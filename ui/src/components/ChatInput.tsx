"use client";

import { useState, useRef, useCallback, KeyboardEvent } from "react";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled: boolean;
  placeholder: string;
}

export function ChatInput({ onSend, disabled, placeholder }: ChatInputProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [text, disabled, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 160) + "px";
    }
  }, []);

  return (
    <div className="px-5 py-4 border-t border-grim-border bg-grim-surface shrink-0">
      <div className="flex gap-2.5 items-end max-w-[900px] mx-auto">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            handleInput();
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          rows={1}
          className="flex-1 bg-grim-bg border border-grim-border rounded-lg px-4 py-3 text-grim-text text-[13.5px] font-mono resize-none outline-none min-h-[44px] max-h-[160px] leading-relaxed transition-colors focus:border-grim-accent placeholder:text-grim-text-dim disabled:opacity-50"
          autoFocus
        />
        <button
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          className="bg-grim-accent border-none rounded-lg px-5 py-2.5 text-white text-[13px] font-semibold cursor-pointer transition-all hover:bg-grim-accent-dim disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
        >
          Send
        </button>
      </div>
      <div className="text-center text-[10px] text-grim-text-dim mt-2">
        Enter to send &middot; Shift+Enter for newline
      </div>
    </div>
  );
}
