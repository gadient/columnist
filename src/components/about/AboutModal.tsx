// About Columnist — renders the project docs verbatim so the in-app pages can never lag the
// source. Each tab imports a Markdown file at build time (Vite `?raw`); editing the doc updates the
// page on the next build. There is no second copy of the content to drift.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { marked } from 'marked';
import { useTheme } from '../../theme/ThemeContext';

// The single source of truth for each page. Change the .md, the page follows.
import productRoadmap from '../../../docs/roadmap.md?raw';
import architecture from '../../../docs/design/architecture.md?raw';
import techStack from '../../../docs/design/TECH-STACK.md?raw';

const TABS = [
  { key: 'product', label: 'Product Roadmap', md: productRoadmap },
  { key: 'architecture', label: 'Architecture', md: architecture },
  { key: 'stack', label: 'Tech Stack', md: techStack },
] as const;

marked.setOptions({ gfm: true });

export const AboutModal = ({ onClose }: { onClose: () => void }) => {
  const [active, setActive] = useState<string>('product');
  const bodyRef = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();

  const current = TABS.find((t) => t.key === active) ?? TABS[0];
  const html = useMemo(() => marked.parse(current.md) as string, [current.md]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Render any ```mermaid blocks (the Architecture page) after the HTML lands in the DOM. Mermaid is
  // dynamically imported so it stays out of the main bundle. Re-runs on tab or theme change.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    el.scrollTop = 0;
    const blocks = Array.from(el.querySelectorAll<HTMLElement>('code.language-mermaid'));
    if (blocks.length === 0) return;
    let cancelled = false;
    (async () => {
      const mermaid = (await import('mermaid')).default;
      mermaid.initialize({
        startOnLoad: false,
        theme: resolved === 'dark' ? 'dark' : 'default',
        securityLevel: 'strict',
        fontFamily: 'inherit',
      });
      for (let i = 0; i < blocks.length; i++) {
        if (cancelled) break;
        const pre = blocks[i].closest('pre');
        const code = blocks[i].textContent || '';
        try {
          const { svg } = await mermaid.render(`mmd-${active}-${i}-${Date.now()}`, code);
          const wrap = document.createElement('div');
          wrap.className = 'doc-mermaid';
          wrap.innerHTML = svg;
          pre?.replaceWith(wrap);
        } catch {
          // A diagram that fails to parse stays as its raw code block — better than a blank.
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [active, html, resolved]);

  return (
    <div className="fixed inset-0 z-[10000] flex flex-col bg-app" role="dialog" aria-modal="true" aria-label="About Columnist">
      <div className="flex items-center justify-between border-b border-line bg-surface px-5 py-3">
        <h2 className="text-sm font-bold text-fg">About Columnist</h2>
        <button
          onClick={onClose}
          aria-label="Close"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-fg-subtle transition-colors hover:bg-surface-sunken hover:text-fg"
        >
          <X size={18} />
        </button>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-line bg-surface px-3">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setActive(t.key)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
              active === t.key ? 'border-accent text-accent' : 'border-transparent text-fg-muted hover:text-fg'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div ref={bodyRef} className="flex-1 overflow-y-auto">
        <div className="doc-prose mx-auto max-w-4xl px-6 py-8" dangerouslySetInnerHTML={{ __html: html }} />
      </div>

      <div className="border-t border-line bg-surface px-6 py-2 text-center text-xs text-fg-subtle">
        Rendered from the project docs at build time — these pages never lag the source.
      </div>
    </div>
  );
};
