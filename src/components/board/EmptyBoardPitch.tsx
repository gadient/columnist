import React from 'react';

/**
 * The board's empty state, used as onboarding for the note-import feature.
 *
 * Why here rather than a tour, banner or GIF: an empty board is the exact moment someone is
 * wondering what to do, and "turn your meeting notes into cards" is both the answer and the most
 * interesting thing this app does. A GIF would show the feature instead of letting them use it,
 * add weight, and go stale the moment the UI changes.
 *
 * It also needs no dismissal state, no localStorage and no "seen it" flag, because it disappears
 * the moment the board has a card. Onboarding that cleans up after itself.
 */
export const EmptyBoardPitch: React.FC<{ onImport: () => void; canImport: boolean }> = ({
  onImport,
  canImport,
}) => (
  <div className="mb-6 rounded-lg border border-dashed border-line-strong bg-surface/50 px-6 py-8 text-center">
    <p className="text-3xl" aria-hidden="true">📥</p>
    <h3 className="mt-3 text-lg font-semibold text-fg">Turn a meeting note into cards</h3>
    <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-fg-subtle">
      Paste notes from a standup, planning session or call. Columnist proposes cards with owners,
      due dates and priorities — you review each one before anything is created.
    </p>

    {canImport && (
      <button
        onClick={onImport}
        className="mt-5 rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-fg-inverted transition-colors hover:bg-blue-700"
      >
        Import notes
      </button>
    )}

    {/* Says plainly that nothing is created behind your back. The review step is the feature's
        central promise, and stating it up front is what makes people willing to try it. */}
    <p className="mt-4 text-xs text-fg-muted">
      Nothing is added to your board until you approve it.
    </p>

    <p className="mt-1 text-xs text-fg-muted">
      Or add a card manually in any column below.
    </p>
  </div>
);
