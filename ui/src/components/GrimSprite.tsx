"use client";

import { useState, useEffect } from "react";

// Pixel grid traced from reference (22x30)
// 0=empty 1=void(face) 2=dark-shadow 3=mid 4=rim-light 5=eye 6=eyeglow
const G = [
  [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
  [0,0,0,0,0,0,2,3,4,4,4,4,4,4,2,0,0,0,0,0,0,0],
  [0,0,0,0,0,2,3,4,4,4,3,3,3,3,3,2,0,0,0,0,0,0],
  [0,0,0,0,0,3,4,4,4,2,1,1,1,1,3,3,0,0,0,0,0,0],
  [0,0,0,0,0,3,4,4,3,1,1,1,1,1,1,3,3,0,0,0,0,0],
  [0,0,0,0,3,3,4,4,3,1,1,1,1,1,1,3,3,0,0,0,0,0],
  [0,0,0,0,3,3,3,4,3,1,5,1,1,5,1,3,3,0,0,0,0,0],
  [0,0,0,0,3,3,3,4,3,1,6,1,1,6,1,3,3,0,0,0,0,0],
  [0,0,0,0,3,3,3,4,3,1,5,1,1,5,1,3,3,0,0,0,0,0],
  [0,0,0,0,0,3,3,3,4,3,1,1,1,1,1,3,3,0,0,0,0,0],
  [0,0,0,0,0,0,2,3,3,3,3,1,1,1,3,3,0,0,0,0,0,0],
  [0,0,0,0,0,0,0,2,3,3,3,3,3,3,3,2,0,0,0,0,0,0],
  [0,0,0,0,0,3,3,4,4,4,4,4,4,4,4,4,4,0,0,0,0,0],
  [0,0,0,0,0,2,3,4,4,4,4,4,4,4,4,4,3,0,0,0,0,0],
  [0,0,0,0,0,2,4,4,4,4,4,4,4,4,4,4,3,0,0,0,0,0],
  [0,0,0,0,0,3,4,4,4,4,4,4,4,4,4,4,3,0,0,0,0,0],
  [0,0,0,0,0,3,4,4,4,4,4,4,3,4,4,4,4,3,0,0,0,0],
  [0,0,0,0,0,3,4,4,4,4,4,4,3,4,4,4,3,3,0,0,0,0],
  [0,0,0,0,2,3,3,4,3,4,3,3,3,3,3,4,3,3,0,0,0,0],
  [0,0,0,0,0,3,2,3,2,3,2,2,2,3,2,3,3,0,0,0,0,0],
  [0,0,0,0,0,3,2,0,2,0,0,0,0,0,2,3,0,0,0,0,0,0],
  [0,0,0,0,0,3,3,0,0,0,0,0,0,0,0,3,0,0,0,0,0,0],
  [0,0,0,0,0,2,3,0,0,0,0,0,0,0,0,2,0,0,0,0,0,0],
  [0,0,0,0,0,0,3,0,0,0,0,0,0,0,0,3,0,0,0,0,0,0],
  [0,0,0,0,0,0,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
];

const PALETTE: Record<number, string> = {
  1: "#0b1018",
  2: "#1a2b3c",
  3: "#2a4058",
  4: "#3e5c72",
  5: "#88b8cc",
  6: "#cce8fa",
};

interface GrimSpriteProps {
  size?: "sm" | "md" | "lg";
}

export function GrimSprite({ size = "md" }: GrimSpriteProps) {
  const [tick, setTick] = useState(0);

  const px = size === "sm" ? 1.5 : size === "md" ? 3 : 6;
  const bob = size === "sm" ? 0 : Math.sin(tick * 0.8) * 2 + Math.sin(tick * 1.5) * 0.8;
  const eyePulse = 0.55 + Math.sin(tick * 1.0) * 0.28;

  useEffect(() => {
    let t = 0;
    const id = setInterval(() => {
      t += 0.03;
      setTick(t);
    }, 28);
    return () => clearInterval(id);
  }, []);

  function cellStyle(cell: number, ry: number) {
    const isTail = ry >= 19;
    const fadeAmt = isTail ? Math.max(0, 1 - (ry - 19) / 7) : 1;
    const waveBoost = isTail ? Math.sin(tick * 0.85 + ry * 0.6) * 0.2 : 0;
    const op = Math.min(1, Math.max(0, fadeAmt + waveBoost));

    if (cell === 6) return {
      background: PALETTE[6],
      boxShadow: size !== "sm"
        ? `0 0 ${2 + eyePulse * 3}px ${PALETTE[6]}, 0 0 ${4 + eyePulse * 6}px #88ccdd`
        : undefined,
      opacity: 0.7 + eyePulse * 0.3,
    };
    if (cell === 5) return { background: PALETTE[5], opacity: 0.9 };
    if (cell === 4) return { background: PALETTE[4], opacity: op };
    if (cell === 3) return { background: PALETTE[3], opacity: op };
    if (cell === 2) return { background: PALETTE[2], opacity: op };
    return { background: PALETTE[1], opacity: 1 };
  }

  function rowTranslate(ry: number) {
    if (ry < 20 || size === "sm") return 0;
    const t = Math.min((ry - 20) / 6, 1);
    return Math.sin(tick * 0.75 + ry * 0.7) * t * 1.5;
  }

  // Trim empty rows at top/bottom for compact rendering
  const trimmed = G.slice(1, 25);

  return (
    <div
      style={{ transform: `translateY(${bob}px)` }}
      className="inline-block"
    >
      {trimmed.map((row, ry) => (
        <div
          key={ry}
          style={{
            display: "flex",
            transform: `translateX(${rowTranslate(ry + 1)}px)`,
          }}
        >
          {row.map((cell, cx) => {
            if (cell === 0) return <div key={cx} style={{ width: px, height: px }} />;
            const s = cellStyle(cell, ry + 1);
            return (
              <div
                key={cx}
                style={{
                  width: px,
                  height: px,
                  background: s.background,
                  boxShadow: s.boxShadow || "none",
                  opacity: s.opacity,
                  imageRendering: "pixelated" as const,
                }}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}
