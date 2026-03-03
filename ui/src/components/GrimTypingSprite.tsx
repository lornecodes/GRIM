"use client";

import { useState, useEffect } from "react";

// Body grid from grim-typing.jsx (18x15)
// 0=empty 1=void 2=dark 3=mid 4=rim 5=eye 6=eyeglow
const BODY = [
  [0, 0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 0, 0, 0, 0, 0],
  [0, 0, 0, 0, 0, 3, 3, 4, 4, 4, 4, 3, 0, 0, 0, 0, 0, 0],
  [0, 0, 0, 0, 3, 4, 4, 3, 1, 1, 1, 2, 3, 0, 0, 0, 0, 0],
  [0, 0, 0, 4, 3, 4, 4, 2, 1, 1, 1, 1, 2, 3, 0, 0, 0, 0],
  [0, 0, 0, 3, 3, 3, 4, 2, 1, 5, 1, 5, 2, 3, 0, 0, 0, 0],
  [0, 0, 0, 3, 3, 3, 4, 2, 1, 6, 1, 6, 3, 3, 0, 0, 0, 0],
  [0, 0, 0, 3, 3, 3, 4, 2, 1, 5, 1, 5, 2, 3, 0, 0, 0, 0],
  [0, 0, 0, 0, 2, 3, 3, 3, 2, 1, 1, 2, 3, 3, 0, 0, 0, 0],
  [0, 0, 0, 0, 0, 0, 2, 3, 3, 3, 3, 3, 2, 0, 0, 0, 0, 0],
  [0, 0, 0, 0, 0, 3, 4, 4, 4, 4, 4, 4, 4, 3, 0, 0, 0, 0],
  [0, 0, 0, 0, 3, 4, 4, 4, 4, 4, 4, 4, 4, 3, 0, 0, 0, 0],
  [0, 0, 0, 0, 3, 4, 4, 4, 4, 4, 4, 4, 4, 3, 3, 0, 0, 0],
  [0, 0, 0, 0, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 0, 0, 0],
  [0, 0, 0, 2, 3, 3, 4, 4, 3, 3, 3, 3, 4, 3, 2, 0, 0, 0],
  [0, 0, 0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 0, 0, 0, 0],
];

const PALETTE: Record<number, string> = {
  1: "#0b1018",
  2: "#1a2b3c",
  3: "#2a4058",
  4: "#3e5c72",
  5: "#88b8cc",
  6: "#cce8fa",
};

const TOP_ROW_KEYS = 7;
const HOME_ROW_KEYS = 6;

interface GrimTypingSpriteProps {
  size?: "xs" | "sm" | "md";
  className?: string;
}

export function GrimTypingSprite({ size = "sm", className }: GrimTypingSpriteProps) {
  const [tick, setTick] = useState(0);
  const [pressedKey, setPressedKey] = useState<{ row: number; col: number } | null>(null);

  const px = size === "xs" ? 1.5 : size === "sm" ? 2 : 3;
  const kpx = size === "xs" ? 5 : size === "sm" ? 6 : 8;
  const kgap = size === "xs" ? 1.5 : size === "sm" ? 2 : 3;
  const bob = size === "xs" ? 0 : Math.sin(tick * 2.0) * (size === "sm" ? 0.6 : 1.2);
  const eyePulse = 0.6 + Math.sin(tick * 1.2) * 0.25;

  useEffect(() => {
    let t = 0;
    let kt = 0;
    const id = setInterval(() => {
      t += 0.04;
      kt++;
      setTick(t);

      // Random key press every ~6 ticks
      if (kt % 6 === 0) {
        const row = Math.random() > 0.35 ? 0 : 1;
        const col = Math.floor(Math.random() * (row === 0 ? TOP_ROW_KEYS : HOME_ROW_KEYS));
        setPressedKey({ row, col });
        setTimeout(() => setPressedKey(null), 90);
      }
    }, 40);
    return () => clearInterval(id);
  }, []);

  const bodyW = BODY[0].length * px;
  const kbW = TOP_ROW_KEYS * (kpx + kgap) - kgap + (size === "xs" ? 4 : 6);
  const kbLeft = Math.round((bodyW - kbW) / 2);

  function pixelStyle(cell: number) {
    if (cell === 0) return { width: px, height: px, background: "transparent" };
    const bg = PALETTE[cell] || PALETTE[1];
    const glow =
      cell === 6 && size !== "xs"
        ? `0 0 ${2 + eyePulse * 4}px ${PALETTE[6]}, 0 0 ${6 + eyePulse * 6}px #88ccdd`
        : "none";
    return { width: px, height: px, background: bg, boxShadow: glow };
  }

  function Key({ row, col, w = kpx }: { row: number; col: number; w?: number }) {
    const isPressed = pressedKey?.row === row && pressedKey?.col === col;
    return (
      <div
        style={{
          width: w,
          height: kpx,
          background: isPressed ? "#3a6a88" : "#1e3a52",
          border: `1px solid ${isPressed ? "#5a9abb" : "#2e5470"}`,
          boxShadow: isPressed ? "0 0 4px #5a9abb66" : "inset 0 -1px 0 #0e1e2e",
          borderRadius: 1,
          flexShrink: 0,
          transition: "background 0.05s, box-shadow 0.05s",
        }}
      />
    );
  }

  return (
    <div
      className={className}
      style={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        transform: `translateY(${bob}px)`,
      }}
    >
      {/* Body */}
      <div>
        {BODY.map((row, ry) => (
          <div key={ry} style={{ display: "flex" }}>
            {row.map((cell, cx) => (
              <div key={cx} style={pixelStyle(cell)} />
            ))}
          </div>
        ))}
      </div>

      {/* Keyboard */}
      <div
        style={{
          marginTop: size === "xs" ? 1 : 2,
          background: "#0e2030",
          border: "1px solid #1e3a52",
          borderRadius: 2,
          padding: size === "xs" ? "2px 3px" : "3px 4px",
          display: "flex",
          flexDirection: "column",
          gap: kgap,
          width: kbW,
        }}
      >
        {/* Top row */}
        <div style={{ display: "flex", gap: kgap }}>
          {Array.from({ length: TOP_ROW_KEYS }, (_, i) => (
            <Key key={i} row={0} col={i} />
          ))}
        </div>
        {/* Home row */}
        <div style={{ display: "flex", gap: kgap, marginLeft: size === "xs" ? 2 : 3 }}>
          {Array.from({ length: HOME_ROW_KEYS }, (_, i) => (
            <Key key={i} row={1} col={i} />
          ))}
        </div>
        {/* Spacebar */}
        <div style={{ display: "flex", justifyContent: "center" }}>
          <Key row={2} col={0} w={kpx * 4 + kgap * 3} />
        </div>
      </div>
    </div>
  );
}
