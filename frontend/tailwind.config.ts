import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

// shadcn/ui theme. Colors resolve to CSS variables declared in src/index.css,
// which means changing the palette is a single-file edit there.
export default {
  // Editorial Mono ships a single canonical light theme — no dark variant.
  darkMode: false,
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    container: { center: true, padding: "2rem", screens: { "2xl": "1400px" } },
    extend: {
      fontFamily: {
        sans: ["'Geist Sans'", "system-ui", "-apple-system", "sans-serif"],
        mono: ["'Geist Mono'", "'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Semantic action colors used by badges + charts. Palette follows
        // the Kyde dark/light scheme; each maps to a pair of HSL triplets
        // defined in index.css.
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        info: "hsl(var(--info))",
        // Custom Editorial Mono tokens.
        "text-base": "hsl(var(--text-base))",
        "text-faint": "hsl(var(--text-faint))",
        "brand-yellow": "hsl(var(--brand-yellow))",
        "brand-green": "hsl(var(--brand-green))",
        "brand-mist": {
          DEFAULT: "hsl(var(--brand-mist))",
          foreground: "hsl(var(--brand-mist-foreground))",
        },
        // Severity fg colors — flat-pill badges resolve their bg via inline vars.
        sev: {
          critical: "hsl(var(--sev-critical-fg))",
          high: "hsl(var(--sev-high-fg))",
          medium: "hsl(var(--sev-medium-fg))",
          low: "hsl(var(--sev-low-fg))",
        },
        // Status axis fg colors (DESIGN-2 §6.6) — for dots/icons; full badges
        // use the .badge-status* component classes.
        status: {
          neutral: "var(--status-neutral-fg)",
          active: "var(--status-active-fg)",
          ok: "var(--status-ok-fg)",
          bad: "var(--status-bad-fg)",
        },
        chart: {
          "1": "hsl(var(--chart-1))",
          "2": "hsl(var(--chart-2))",
          "3": "hsl(var(--chart-3))",
          "4": "hsl(var(--chart-4))",
          "5": "hsl(var(--chart-5))",
          line: "hsl(var(--chart-line))",
          grid: "hsl(var(--chart-grid))",
          axis: "hsl(var(--chart-axis))",
          marker: "hsl(var(--chart-marker))",
          track: "hsl(var(--chart-track))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: {
        // Floating layers only (popover / dropdown / tooltip / dialog).
        popover: "0 8px 28px rgba(0,0,0,.10), 0 0 0 1px rgba(0,0,0,.05)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "pulse-dot": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.4" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "pulse-dot": "pulse-dot 2s cubic-bezier(.4,0,.6,1) infinite",
      },
    },
  },
  plugins: [animate],
} satisfies Config;
