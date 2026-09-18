import React from 'react';

/**
 * "How the assistant works" — companion to `HowItWorks` (notes → cards), same modal shape and the
 * same recreate-the-UI-rather-than-screenshot approach, for the same reasons.
 *
 * The content is deliberately weighted towards *limits*. The notes flow earns trust by showing a
 * review step; the assistant has no review step, so what builds trust here is being precise about
 * what it can reach, what it cannot do at all, and where it is still weak. Overselling an alpha
 * feature to early users costs more than underselling it.
 */

type Step = {
  n: number;
  title: string;
  caption: string;
  mock: React.ReactNode;
};

const Frame: React.FC<{ dark?: boolean; children: React.ReactNode }> = ({ dark, children }) => (
  <div
    className={`overflow-hidden rounded-lg border shadow-sm ${
      dark ? 'border-line bg-surface' : 'border-line bg-surface'
    }`}
  >
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

const Bubble: React.FC<{ who: 'you' | 'it'; children: React.ReactNode }> = ({ who, children }) => (
  <div
    className={`max-w-[85%] rounded-2xl px-3 py-2 text-[11px] leading-relaxed ${
      who === 'you'
        ? 'ml-auto bg-blue-600 text-fg-inverted'
        : 'mr-auto border border-line bg-surface-raised text-fg'
    }`}
  >
    {children}
  </div>
);

const STEPS: Step[] = [
  {
    n: 1,
    title: 'Open the panel and ask in plain English',
    caption:
      'The tab sits on the right edge — inside a board, or across a whole workspace. No commands or syntax: ask the way you would ask a colleague.',
    mock: (
      <Frame dark>
        <div className="space-y-2">
          <Bubble who="you">What should I focus on this week?</Bubble>
          <Bubble who="it">Two cards are high priority and due before Friday…</Bubble>
        </div>
      </Frame>
    ),
  },
  {
    n: 2,
    title: 'It reads your cards to answer — it does not guess',
    caption:
      'Every answer comes from looking at your actual cards. If they do not say it, neither will the assistant.',
    mock: (
      <Frame>
        <div className="space-y-1.5">
          {[
            'What is overdue, and what is due soon',
            'What is high priority right now',
            'Who has the most on their plate',
            'What is blocked, and what is waiting on it',
            'How much the team has finished lately',
          ].map((line) => (
            <div key={line} className="rounded-md border border-line bg-surface-sunken px-2 py-1.5">
              <span className="text-[11px] text-fg-muted">{line}</span>
            </div>
          ))}
        </div>
      </Frame>
    ),
  },
  {
    n: 3,
    title: 'It looks where you are',
    caption:
      'Open it inside a board and it answers about that board. Open it in a workspace and it answers across every board in it — which is where questions like “what is slipping this week?” get interesting.',
    mock: (
      <Frame>
        <div className="space-y-1.5">
          <div className="rounded-md border border-blue-300 bg-blue-50 px-2 py-1.5">
            <p className="text-[11px] font-medium text-gray-800">In a workspace</p>
            <p className="text-[10px] text-gray-600">Every board in it, compared side by side</p>
          </div>
          <div className="rounded-md border border-green-300 bg-green-50 px-2 py-1.5">
            <p className="text-[11px] font-medium text-gray-800">In a board</p>
            <p className="text-[10px] text-gray-600">Just that board, nothing else in the way</p>
          </div>
          <p className="pt-0.5 text-[10px] italic text-fg-muted">
            The panel says which one it is using, above the conversation.
          </p>
        </div>
      </Frame>
    ),
  },
  {
    n: 4,
    title: 'It cannot change anything',
    caption: 'It reads. It never creates, moves, edits or deletes a card.',
    mock: (
      <Frame>
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md border border-green-300 bg-green-50 p-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-green-700">Can</p>
            <p className="mt-1 text-[11px] text-gray-700">Read, count, summarise, compare</p>
          </div>
          <div className="rounded-md border border-line-strong bg-surface-sunken p-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-fg-muted">Cannot</p>
            <p className="mt-1 text-[11px] text-fg-muted">Create, move, edit, delete</p>
          </div>
        </div>
      </Frame>
    ),
  },
  {
    n: 5,
    title: 'If it cannot finish, it says so',
    caption:
      'Answers are bounded — a number of lookups, a token budget, a time limit. Hitting one stops the answer rather than shipping a half-formed one, so a reply that arrives is a reply that finished.',
    mock: (
      <Frame dark>
        <div className="space-y-2">
          <Bubble who="you">Compare every board and rank them by risk</Bubble>
          <Bubble who="it">I couldn&apos;t finish looking into that. Try narrowing it to one board.</Bubble>
        </div>
      </Frame>
    ),
  },
];

export const HowChatWorks: React.FC<{ onClose: () => void }> = ({ onClose }) => (
  <div
    className="fixed inset-0 z-[10000] flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8"
    role="dialog"
    aria-modal="true"
    aria-label="How the assistant works"
    onClick={onClose}
  >
    <div
      className="w-full max-w-3xl rounded-2xl bg-surface p-6 shadow-xl sm:p-8"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-2xl font-bold text-fg">Asking the assistant</h2>
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700">
              Alpha
            </span>
          </div>
          <p className="mt-1 text-sm text-fg-muted">
            It reads your boards and answers questions. It follows the current conversation, so you
            can ask a follow-up — but it starts fresh when you switch board or workspace, and it
            cannot change anything.
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
            {s.mock}
          </li>
        ))}
      </ol>

      {/* The caveats live in the panel itself now — a persistent "work in progress" strip above
          every conversation, plus the Alpha badge. Repeating them here turned an explainer into an
          apology, and the strip is the one a user actually reads at the moment it matters. */}
      <div className="mt-7 flex items-center justify-between border-t border-line pt-5">
        <p className="text-xs text-fg-muted">Reads only · answers where you are · says so if it cannot finish</p>
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
