import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: [
          "var(--font-jetbrains)",
          "JetBrains Mono",
          "SF Mono",
          "Cascadia Code",
          "Fira Code",
          "monospace",
        ],
      },
      colors: {
        grim: {
          bg: "#0a0a0f",
          surface: "#12121a",
          "surface-hover": "#1a1a25",
          border: "#2a2a3a",
          text: "#e0e0e8",
          "text-dim": "#8888a0",
          accent: "#7c6fef",
          "accent-dim": "#5a4fd0",
          "user-bg": "#1e1e30",
          "grim-bg": "#14141e",
          success: "#4ade80",
          warning: "#fbbf24",
          error: "#f87171",
          "trace-bg": "#0d0d14",
        },
        trace: {
          node: "#60a5fa",
          llm: "#c084fc",
          tool: "#34d399",
          graph: "#f59e0b",
        },
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pulse-dot": {
          "0%, 80%, 100%": { opacity: "0.3" },
          "40%": { opacity: "1" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
        "pulse-dot": "pulse-dot 1.2s infinite",
      },
    },
  },
  plugins: [typography],
};

export default config;
