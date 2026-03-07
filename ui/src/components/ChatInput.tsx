"use client";

import { useState, useRef, useCallback, KeyboardEvent } from "react";

interface FileAttachment {
  name: string;
  type: string;
  size: number;
  content: string; // base64
}

interface ChatInputProps {
  onSend: (text: string, files?: FileAttachment[]) => void;
  onCancel: () => void;
  isStreaming: boolean;
  queuedCount: number;
  disabled: boolean;
  placeholder: string;
}

const MAX_FILE_SIZE = 50 * 1024; // 50KB
const MAX_FILES = 5;

export type { FileAttachment };

export function ChatInput({
  onSend,
  onCancel,
  isStreaming,
  queuedCount,
  disabled,
  placeholder,
}: ChatInputProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<FileAttachment[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed, attachments.length > 0 ? attachments : undefined);
    setText("");
    setAttachments([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [text, disabled, onSend, attachments]);

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

  const handleFiles = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;

    const newAttachments: FileAttachment[] = [];
    for (let i = 0; i < Math.min(files.length, MAX_FILES - attachments.length); i++) {
      const file = files[i];
      if (file.size > MAX_FILE_SIZE) continue;

      const content = await new Promise<string>((resolve) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = reader.result as string;
          // Strip the data:...;base64, prefix
          resolve(result.split(",")[1] || "");
        };
        reader.readAsDataURL(file);
      });

      newAttachments.push({
        name: file.name,
        type: file.type || "text/plain",
        size: file.size,
        content,
      });
    }

    setAttachments((prev) => [...prev, ...newAttachments].slice(0, MAX_FILES));
    // Reset input so the same file can be re-selected
    e.target.value = "";
  }, [attachments.length]);

  const removeAttachment = useCallback((name: string) => {
    setAttachments((prev) => prev.filter((f) => f.name !== name));
  }, []);

  return (
    <div className="px-5 py-3 border-t border-grim-border bg-grim-bg shrink-0">
      {/* Attached files */}
      {attachments.length > 0 && (
        <div className="flex gap-1.5 flex-wrap mb-2 max-w-[900px] mx-auto pl-5">
          {attachments.map((f) => (
            <span
              key={f.name}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-grim-surface text-grim-text-dim border border-grim-border"
            >
              {f.name}
              <button
                onClick={() => removeAttachment(f.name)}
                className="text-grim-text-dim hover:text-grim-text ml-0.5"
              >
                x
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Queue indicator */}
      {queuedCount > 0 && (
        <div className="text-[10px] text-grim-accent font-mono mb-1 max-w-[900px] mx-auto pl-5">
          {queuedCount} message{queuedCount > 1 ? "s" : ""} queued
        </div>
      )}

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

        {/* File attach button */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={handleFiles}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          className="shrink-0 p-1 text-grim-text-dim hover:text-grim-text transition-colors"
          title="Attach files"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 8.5l-5.5 5.5a3.5 3.5 0 01-5-5L9 3.5a2.5 2.5 0 013.5 3.5L7 12.5a1.5 1.5 0 01-2-2L10.5 5" />
          </svg>
        </button>

        {/* Stop button (visible during streaming) */}
        {isStreaming && (
          <button
            onClick={onCancel}
            className="shrink-0 px-2 py-0.5 rounded text-[11px] font-mono font-bold bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/30 transition-colors"
            title="Stop response"
          >
            Stop
          </button>
        )}
      </div>
    </div>
  );
}
