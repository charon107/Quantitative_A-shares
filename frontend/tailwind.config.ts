import type { Config } from "tailwindcss";

// Anthropic 浅色暖调设计 token
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        cream: "#FAF9F5", // 页面背景（奶油）
        panel: "#FFFFFF", // 卡片
        panel2: "#F0EEE6", // 次级面板 / 浅米
        ink: "#1A1A18", // 主文字（墨色）
        muted: "#6B6760", // 次文字
        line: "#E5E1D8", // 暖灰边框
        clay: "#CC785C", // 强调（book cloth 陶土）
        clayDark: "#BD5D3A", // 强调 hover
        up: "#3A8C5F", // 上涨（沉稳绿）
        down: "#C84B31", // 下跌（陶红）
      },
      fontFamily: {
        serif: ['"Source Serif 4"', '"Noto Serif SC"', "Georgia", "serif"],
        sans: ['"Inter"', '"Noto Sans SC"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      boxShadow: {
        soft: "0 1px 3px rgba(26,26,24,0.06), 0 1px 2px rgba(26,26,24,0.04)",
        lift: "0 4px 16px rgba(26,26,24,0.08)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
    },
  },
  plugins: [],
} satisfies Config;
