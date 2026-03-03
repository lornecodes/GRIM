"use client";

import { useState, useRef, useCallback, useEffect } from "react";

interface KnowledgeTurnSliderProps {
  maxTurn: number;
  currentTurn: number;
  onTurnChange: (turn: number) => void;
  totalNodes: number;
}

export function KnowledgeTurnSlider({
  maxTurn,
  currentTurn,
  onTurnChange,
  totalNodes,
}: KnowledgeTurnSliderProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const play = useCallback(() => {
    setIsPlaying(true);
    onTurnChange(0);
  }, [onTurnChange]);

  const pause = useCallback(() => {
    setIsPlaying(false);
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  // Auto-advance during playback
  useEffect(() => {
    if (!isPlaying) return;

    timerRef.current = setInterval(() => {
      onTurnChange(currentTurn + 1);
    }, 800);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isPlaying, currentTurn, onTurnChange]);

  // Stop when we reach the end
  useEffect(() => {
    if (isPlaying && currentTurn >= maxTurn) {
      pause();
    }
  }, [isPlaying, currentTurn, maxTurn, pause]);

  if (maxTurn <= 0) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-2 border-t border-grim-border/30 bg-grim-surface/80">
      <button
        onClick={isPlaying ? pause : play}
        className="text-[11px] px-2 py-0.5 rounded bg-grim-grim-bg/60 border border-grim-border/30 hover:border-grim-border/60 text-grim-text-dim transition-colors"
      >
        {isPlaying ? "⏸" : "▶"}
      </button>
      <input
        type="range"
        min={0}
        max={maxTurn}
        value={currentTurn}
        onChange={(e) => onTurnChange(Number(e.target.value))}
        className="flex-1 h-1 accent-grim-accent cursor-pointer"
      />
      <span className="text-[10px] text-grim-text-dim whitespace-nowrap">
        turn {currentTurn}/{maxTurn} · {totalNodes} concepts
      </span>
    </div>
  );
}
