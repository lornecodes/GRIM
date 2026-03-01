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
    <div className="px-5 py-3 border-t border-grim-border bg-grim-bg shrink-0">
      <div className="flex items-start gap-2 max-w-[900px] mx-auto">
        <span className="text-grim-accent font-mono text-sm shrink-0 leading-relaxed pt-[2px] select-none font-bold">
          &gt;
        </span>
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
          className="flex-1 bg-transparent outline-none border-none text-grim-text text-[13.5px] font-mono resize-none min-h-[28px] max-h-[160px] leading-relaxed caret-[#7c6fef] placeholder:text-grim-text-dim disabled:opacity-50"
          autoFocus
        />
      </div>
    </div>
  );
}
