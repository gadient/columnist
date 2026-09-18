import React from 'react';
import { DEMO_NOTES } from '../../data/demoNotes';

/**
 * "How it works" — the note-to-cards flow in five steps, opened from the landing screen.
 *
 * Each step is a RECREATION of the UI rather than a screenshot. Screenshots of an actively
 * changing UI go stale quickly, need hosting, and shrink the moments that matter -- the flagged
 * card and the owner suggestion -- to a size where they cannot be read. Markup stays correct,
 * weighs nothing, and can show those details full size.
 *
 * Every step accepts an optional `image`. Drop a real screenshot in and it replaces the mock with
 * no restructuring, so this is a starting point rather than a commitment.
 */

type Step = {
  n: number;
  title: string;
  caption: string;
  image?: string;              // optional real screenshot; replaces the mock when present
  mock: React.ReactNode;
};

// --- little primitives shared by the mocks -----------------------------------------------------

const Frame: React.FC<{ dark?: boolean; children: React.ReactNode }> = ({ dark, children }) => (
  <div
    className={`overflow-hidden rounded-lg border shadow-sm ${
      dark ? 'border-line bg-surface' : 'border-line bg-surface'
    }`}
  >
    {/* window chrome, so a mock reads as "a screen" without pretending to be a photo */}
    <div
      className={`flex items-center gap-1.5 border-b px-3 py-2 ${
        dark ? 'border-line bg-surface-raised' : 'border-line bg-surface-sunken'
      }`}
    >
      <span className="h-2 w-2 rounded-full bg-red-400" />
      <span className="h-2 w-2 rounded-full bg-yellow-400" />
      <span className="h-2 w-2 rounded-full bg-green-400" />
    </div>
    <div className="p-3">{children}</div>
  </div>
);

const MiniCard: React.FC<{ title: string; who?: string; tone?: 'plain' | 'new' }> = ({
  title, who, tone = 'plain',
}) => (
  <div
    className={`rounded-md border px-2.5 py-2 ${
      tone === 'new' ? 'border-green-500/60 bg-green-500/10' : 'border-line bg-surface-raised'
    }`}
  >
    <p className="text-[11px] font-medium leading-snug text-fg">{title}</p>
    {who && <p className="mt-1 text-[10px] text-fg-muted">{who}</p>}
  </div>
);

// --- the five steps ----------------------------------------------------------------------------

const NOTE_EXCERPT = DEMO_NOTES.Engineering.split('\n').slice(0, 8).join('\n');

const STEPS: Step[] = [
  {
    n: 1,
    title: 'Open a board',
    caption: 'Load the demo workspace and pick a board — Engineering, say. Everything below happens inside it.',
    mock: (
      <Frame>
        <div className="grid grid-cols-3 gap-2">
          {([['Engineering', 6], ['Engineering × PM', 5], ['PM × Sales', 5]] as const).map(([t, n], i) => (
            <div
              key={t}
              className={`rounded-md border p-2 ${i === 0 ? 'border-blue-500 bg-blue-50' : 'border-line bg-surface'}`}
            >
              <p className="text-[11px] font-semibold text-fg">{t}</p>
              <p className="mt-1 text-[10px] text-fg-muted">{n} cards</p>
            </div>
          ))}
        </div>
      </Frame>
    ),
  },
  {
    n: 2,
    title: 'Hit Import notes',
    caption: 'The button sits at the top right of every board.',
    mock: (
      <Frame dark>
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-fg">Engineering</p>
            <p className="text-[10px] text-fg-muted">Core platform engineering tasks and sprint work</p>
          </div>
          <span className="shrink-0 rounded-md bg-blue-600 px-2.5 py-1.5 text-[10px] font-semibold text-fg-inverted">
            📥 Import notes
          </span>
        </div>
      </Frame>
    ),
  },
  {
    n: 3,
    title: 'Paste the note',
    caption:
      'Any meeting note — standup, planning, a call. On demo boards a sample note is one click away, so you can try it without finding one.',
    mock: (
      <Frame>
        <pre className="max-h-40 overflow-hidden whitespace-pre-wrap rounded-md border border-line bg-surface-sunken p-2 text-[10px] leading-relaxed text-fg-muted">
{NOTE_EXCERPT}
        </pre>
        <p className="mt-2 text-[10px] font-medium text-blue-600">Use a sample note from this board</p>
      </Frame>
    ),
  },
  {
    n: 4,
    title: 'Review what it proposes',
    caption:
      'Cards arrive with owners, dates and priorities. Anything it could not pin down — an owner that is not on the board, a quote it could not find in your note — is held back with a reason. Nothing is created yet.',
    mock: (
      <Frame>
        <div className="space-y-2">
          <div className="rounded-md border border-green-300 bg-green-50 p-2">
            <p className="text-[11px] font-semibold text-gray-800">Write the migration plan for the orders table</p>
            <p className="mt-1 text-[10px] text-gray-600">Alex Kim · due the 30th · ready to add</p>
          </div>
          <div className="rounded-md border border-green-300 bg-green-50 p-2">
            <p className="text-[11px] font-semibold text-gray-800">Benchmark the new caching layer</p>
            <p className="mt-1 text-[10px] text-gray-600">Marcus Chen · no due date · ready to add</p>
          </div>
          <div className="rounded-md border border-amber-300 bg-amber-50 p-2">
            <p className="text-[11px] font-semibold text-gray-800">Review retry logic in the webhook consumer</p>
            <p className="mt-1 text-[10px] text-gray-700">
              Held back — no exact match for “<b>Priya</b>”. Did you mean <b>Priya Sharma</b>?
            </p>
          </div>
        </div>
      </Frame>
    ),
  },
  {
    n: 5,
    title: 'Approve, and the cards appear',
    caption:
      'Add the clean ones in one click, decide on the rest, and undo the whole batch if you change your mind.',
    mock: (
      <Frame dark>
        <div className="grid grid-cols-3 gap-2">
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">Backlog</p>
            <div className="space-y-1.5">
              <MiniCard title="Write the migration plan" who="Alex Kim" tone="new" />
              <MiniCard title="Benchmark caching layer" who="Marcus Chen" tone="new" />
            </div>
          </div>
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">In Progress</p>
            <div className="space-y-1.5">
              <MiniCard title="Review retry logic" who="Priya Sharma" tone="new" />
              <MiniCard title="Refactor auth middleware" who="Alex Kim" />
            </div>
          </div>
          <div>
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-fg-muted">In Review</p>
            <div className="space-y-1.5">
              <MiniCard title="Fix race condition" who="Marcus Chen" />
            </div>
          </div>
        </div>
      </Frame>
    ),
  },
];

export const HowItWorks: React.FC<{ onClose: () => void }> = ({ onClose }) => (
  <div
    className="fixed inset-0 z-[10000] flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8"
    role="dialog"
    aria-modal="true"
    aria-label="How it works"
    onClick={onClose}
  >
    <div
      className="w-full max-w-3xl rounded-2xl bg-surface p-6 shadow-xl sm:p-8"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-fg">From meeting notes to cards</h2>
          <p className="mt-1 text-sm text-fg-muted">
            Five steps. You approve every card before it reaches your board.
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="shrink-0 rounded-md px-2 py-1 text-xl leading-none text-fg-subtle hover:bg-surface-sunken hover:text-fg-muted"
        >
          ×
        </button>
      </div>

      <ol className="mt-6 space-y-6">
        {STEPS.map((s) => (
          <li key={s.n} className="grid gap-3 sm:grid-cols-[1fr_1.15fr] sm:items-start sm:gap-5">
            <div>
              <div className="flex items-center gap-2">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 text-[11px] font-bold text-fg-inverted">
                  {s.n}
                </span>
                <h3 className="text-sm font-semibold text-fg">{s.title}</h3>
              </div>
              <p className="mt-1.5 text-[13px] leading-relaxed text-fg-muted">{s.caption}</p>
            </div>
            {s.image ? (
              <img src={s.image} alt="" className="w-full rounded-lg border border-line shadow-sm" />
            ) : (
              s.mock
            )}
          </li>
        ))}
      </ol>

      <div className="mt-7 flex items-center justify-between border-t border-line pt-5">
        <p className="text-xs text-fg-muted">Works with pasted text, .txt and .docx files.</p>
        <button
          onClick={onClose}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-fg-inverted transition-colors hover:bg-blue-700"
        >
          Got it
        </button>
      </div>
    </div>
  </div>
);
