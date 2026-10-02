// tailwind.config.js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./**/templates/**/*.html",
    "./**/*.py",              // for form widget classes in Python
    "./static/js/**/*.js",
  ],
  darkMode: "class",          // toggle via <html class="dark">
  theme: {
    extend: {
      colors: {
        // Design tokens — tweak these to change your brand
        brand: {
          50:  "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#3b82f6",     // primary
          600: "#2563eb",
          700: "#1d4ed8",
          800: "#1e40af",
          900: "#1e3a8a",
          950: "#172554",
        },
        surface: {
          light: "#ffffff",
          muted: "#f8fafc",
          border: "#e2e8f0",
          dark:   "#0f172a",
          "dark-muted": "#1e293b",
          "dark-border": "#334155",
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        // Denser than default — good for admin/table-heavy apps
        "xs":   ["0.75rem",  { lineHeight: "1rem" }],
        "sm":   ["0.8125rem",{ lineHeight: "1.25rem" }],  // 13px
        "base": ["0.875rem", { lineHeight: "1.375rem" }], // 14px
        "lg":   ["1rem",     { lineHeight: "1.5rem" }],
        "xl":   ["1.125rem", { lineHeight: "1.75rem" }],
      },
      boxShadow: {
        card: "0 1px 3px 0 rgb(0 0 0 / 0.05), 0 1px 2px -1px rgb(0 0 0 / 0.05)",
      },
      borderRadius: {
        DEFAULT: "0.375rem",
        lg: "0.5rem",
      },
    },
  },
  plugins: [],
};