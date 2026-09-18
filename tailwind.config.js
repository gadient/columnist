/** @type {import('tailwindcss').Config} */

// Semantic theme tokens. Each name maps to a CSS variable defined in
// src/index.css, using the `rgb(var(--x) / <alpha-value>)` form so opacity
// modifiers (e.g. bg-surface/70) keep working. Both light and dark values live in
// the CSS; toggling <html data-theme="dark"> flips every token at once.
//
//   bg-app                              app background
//   bg-surface / -raised / -sunken      cards, panels, hovers, input wells
//   text-fg / -muted / -subtle / -inverted   primary → tertiary text, on-accent text
//   border-line / -strong               hairline and stronger borders
//   bg-accent / -hover, text-accent     brand blue
const token = (name) => `rgb(var(${name}) / <alpha-value>)`;

export default {
  darkMode: ['selector', '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        app: token('--bg'),
        surface: {
          DEFAULT: token('--surface'),
          raised: token('--surface-raised'),
          sunken: token('--surface-sunken'),
        },
        fg: {
          DEFAULT: token('--text'),
          muted: token('--text-muted'),
          subtle: token('--text-subtle'),
          inverted: token('--text-inverted'),
        },
        line: {
          DEFAULT: token('--border-default'),
          strong: token('--border-strong'),
        },
        accent: {
          DEFAULT: token('--accent'),
          hover: token('--accent-hover'),
        },
      },
    },
  },
  plugins: [],
};
