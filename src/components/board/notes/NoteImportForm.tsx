import React, { useState } from 'react';
import { toLocalYMD } from '../../../utils/dates';
import type { AnalyzeInput } from '../../../api/noteImports';

// The import form. It gathers exactly what analysis needs and nothing
// the model gets to decide: one source (paste XOR file) and the meeting date that anchors relative
// phrases like "by Friday". There is no column here: the reviewer picks each card's column during
// review, never the model.

type InputMode = 'paste' | 'file';

export const NoteImportForm = ({
  disabled,
  error,
  onAnalyze,
  sampleNote,
}: {
  disabled: boolean;
  error: string | null;
  onAnalyze: (input: AnalyzeInput) => void;
  /** A ready-made note for this board, or null. Only ever supplied for demo-workspace boards, so
      the button below simply does not exist in a user's own workspace. */
  sampleNote?: string | null;
}) => {
  const [mode, setMode] = useState<InputMode>('paste');
  const [pastedText, setPastedText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  // Default the anchor to today — the common case is importing notes from the meeting just held.
  const [meetingDate, setMeetingDate] = useState(toLocalYMD(new Date()));

  const hasSource = mode === 'paste' ? pastedText.trim() !== '' : file !== null;
  const canSubmit = !disabled && hasSource && meetingDate !== '';

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    // No column here: the model can't suggest one, so every card defaults to the board's
    // first column and the reviewer re-targets each card during review.
    onAnalyze({
      meetingDate,
      clientRequestId: crypto.randomUUID(), // suppresses an accidental double-submit
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      ...(mode === 'file' ? { file: file! } : { pastedText }),
    });
  };

  const tabClass = (active: boolean) =>
    `px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
      active ? 'bg-blue-600 text-fg-inverted' : 'bg-surface-sunken text-fg-muted hover:bg-surface-raised'
    }`;

  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-sm text-fg-muted">
        Paste your meeting notes or upload a .txt / .docx file. Nothing is added to the board until
        you review and approve each card.
      </p>

      <div className="flex gap-2">
        <button type="button" className={tabClass(mode === 'paste')} onClick={() => setMode('paste')}>
          Paste text
        </button>
        <button type="button" className={tabClass(mode === 'file')} onClick={() => setMode('file')}>
          Upload file
        </button>
      </div>

      {mode === 'paste' ? (
        <div>
          <textarea
            value={pastedText}
            onChange={(e) => setPastedText(e.target.value)}
            placeholder="Paste meeting notes here…"
            rows={10}
            className="w-full rounded-md border border-line-strong p-3 text-sm text-fg focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {/* Demo workspace only. Fills the box rather than analysing straight away, so the note is
              visible and editable first — the point is to show what a note looks like, not to run a
              black box. */}
          {sampleNote && pastedText.trim() === '' && (
            <button
              type="button"
              onClick={() => setPastedText(sampleNote)}
              className="mt-2 text-xs font-medium text-blue-600 underline-offset-2 hover:underline"
            >
              Use a sample note from this board
            </button>
          )}
        </div>
      ) : (
        <div>
          <input
            type="file"
            accept=".txt,.docx"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-fg-muted file:mr-3 file:rounded-md file:border-0 file:bg-blue-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-blue-700 hover:file:bg-blue-100"
          />
          {file && <p className="mt-1 text-xs text-fg-muted">{file.name}</p>}
        </div>
      )}

      <label className="block sm:max-w-xs">
        <span className="mb-1 block text-sm font-medium text-fg-muted">Meeting date</span>
        <input
          type="date"
          value={meetingDate}
          onChange={(e) => setMeetingDate(e.target.value)}
          className="w-full rounded-md border border-line-strong p-2 text-sm text-fg focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <span className="mt-1 block text-xs text-fg-subtle">Anchors dates like "by Friday". You choose each card's column when you review.</span>
      </label>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-fg-inverted transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-surface-raised disabled:text-fg-subtle"
        >
          {disabled ? 'Analyzing…' : 'Analyze notes'}
        </button>
      </div>
    </form>
  );
};
