import React, { useState, useEffect, useRef } from 'react';
import { Sparkles } from 'lucide-react';
import { ChatExtension } from './ChatExtension';

// Height of the global nav bar rendered by MultiBoardApp (keep in sync with App.tsx NAV_H).
const NAV_TOP = 52;
const MIN_WIDTH = 320;
const DEFAULT_WIDTH = 400;

export const WorkspaceWithChat = ({ children, activeBoardId = null, activeWorkspaceId = null, scopeLabel = null, scopeName = null, onOpenFeedback = null }) => {
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_WIDTH);
  const resizingRef = useRef(false);
  const widthRef = useRef(panelWidth);
  const pointerRef = useRef({ x: 0, width: DEFAULT_WIDTH });

  useEffect(() => {
    widthRef.current = panelWidth;
  }, [panelWidth]);

  // Publish the docked panel width as a CSS variable so full-width fixed
  // overlays (e.g. the Board Insights drawer) can shrink beside the chat panel
  // instead of sliding underneath it.
  useEffect(() => {
    document.documentElement.style.setProperty(
      '--chat-panel-width',
      isChatOpen ? `${panelWidth}px` : '0px'
    );
    return () => {
      document.documentElement.style.setProperty('--chat-panel-width', '0px');
    };
  }, [isChatOpen, panelWidth]);

  useEffect(() => {
    const onPointerMove = (event) => {
      if (!resizingRef.current) return;
      // Dragging the left edge leftwards (smaller clientX) widens the panel.
      const dx = pointerRef.current.x - event.clientX;
      const max = Math.min(720, window.innerWidth - 64);
      const width = Math.max(MIN_WIDTH, Math.min(max, pointerRef.current.width + dx));
      setPanelWidth(width);
    };

    const onPointerUp = () => {
      resizingRef.current = false;
      document.body.style.userSelect = '';
    };

    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', onPointerUp);
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', onPointerUp);
    };
  }, []);

  const startResize = (event) => {
    event.preventDefault();
    resizingRef.current = true;
    pointerRef.current = { x: event.clientX, width: widthRef.current };
    document.body.style.userSelect = 'none';
  };

  return (
    <div className="relative min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 dark:from-gray-900 dark:to-gray-950">
      {/* Content reflows left so the docked panel never covers the board. */}
      <div
        className="min-w-0 transition-[padding] duration-300 ease-out"
        style={{ paddingRight: isChatOpen ? `${panelWidth}px` : 0 }}
      >
        {children}
      </div>

      {/* Edge tab — only shown when the panel is collapsed. */}
      {!isChatOpen && (
        <button
          onClick={() => setIsChatOpen(true)}
          className="fixed right-0 top-1/2 -translate-y-1/2 z-40 flex flex-col items-center gap-1.5 bg-gradient-to-b from-blue-500 to-purple-500 text-fg-inverted pl-2.5 pr-2 py-4 rounded-l-xl shadow-lg hover:shadow-xl hover:pr-3 transition-all"
          title="Open assistant"
        >
          <Sparkles size={18} />
          <span className="text-[11px] font-semibold tracking-wide [writing-mode:vertical-rl] rotate-180">
            Assistant
          </span>
        </button>
      )}

      {/* Docked right-side panel. */}
      <aside
        className="fixed right-0 z-50 flex bg-surface border-l border-line shadow-2xl transition-transform duration-300 ease-out"
        style={{
          top: `${NAV_TOP}px`,
          bottom: 0,
          width: `${panelWidth}px`,
          transform: isChatOpen ? 'translateX(0)' : 'translateX(100%)',
        }}
        aria-hidden={!isChatOpen}
      >
        {/* Left-edge resize handle. */}
        <div
          onPointerDown={startResize}
          className="absolute left-0 top-0 bottom-0 w-1.5 -translate-x-1/2 cursor-ew-resize group"
          title="Resize panel"
        >
          <div className="h-full w-px mx-auto bg-transparent group-hover:bg-blue-500/60 transition-colors" />
        </div>

        <div className="flex-1 min-w-0">
          <ChatExtension
            activeBoardId={activeBoardId}
            activeWorkspaceId={activeWorkspaceId}
            scopeLabel={scopeLabel}
            scopeName={scopeName}
            onClose={() => setIsChatOpen(false)}
            onOpenFeedback={onOpenFeedback}
          />
        </div>
      </aside>
    </div>
  );
};
