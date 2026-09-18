import React, { useEffect, useState } from 'react';
import { apiListFeedback, apiSubmitFeedback, FeedbackCategory, FeedbackView } from '../../api/feedback';

/**
 * In-app feedback — one box, always reachable from the nav.
 *
 * The audience is a handful of invited users who will not file an issue anywhere else. So the
 * form asks for as little as possible: a category, a sentence, and optionally a rating. Everything
 * that can be captured without asking (who they are, which screen they were on) is attached by the
 * client or the server.
 *
 * The "Inbox" tab only appears for the Instance's primary user. That is enforced
 * server-side — `GET /feedback` answers 404 to anyone else — so this component treats a failed load
 * as "not for you" and simply hides the tab, rather than surfacing an error a normal user cannot
 * act on.
 */

const CATEGORIES: { value: FeedbackCategory; label: string; hint: string }[] = [
  { value: 'chat', label: 'Assistant', hint: 'The chat panel' },
  { value: 'notes', label: 'Import notes', hint: 'Meeting notes to cards' },
  { value: 'bug', label: 'Something broke', hint: 'A crash, an error, or the wrong result' },
  { value: 'general', label: 'General', hint: 'Anything else' },
];

const formatWhen = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

export const FeedbackModal: React.FC<{ onClose: () => void; page?: string }> = ({ onClose, page }) => {
  const [category, setCategory] = useState<FeedbackCategory>('general');
  const [message, setMessage] = useState('');
  const [rating, setRating] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [inbox, setInbox] = useState<FeedbackView[] | null>(null);
  const [tab, setTab] = useState<'write' | 'read'>('write');

  // Probe the inbox once. Success means this caller is the primary user; a 404 (the normal case
  // for everyone else) just leaves the tab hidden.
  useEffect(() => {
    let cancelled = false;
    apiListFeedback()
      .then((rows) => { if (!cancelled) setInbox(rows); })
      .catch(() => { /* not the primary user — no tab, no error */ });
    return () => { cancelled = true; };
  }, [sent]);

  const submit = async () => {
    const text = message.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      await apiSubmitFeedback({ category, message: text, rating, page: page || null });
      setSent(true);
      setMessage('');
      setRating(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-start justify-center overflow-y-auto bg-black/50 p-4 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label="Send feedback"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl rounded-2xl bg-surface p-6 shadow-xl sm:p-7"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-bold text-fg">Send feedback</h2>
            <p className="mt-1 text-sm text-fg-muted">
              This is an early build. Blunt is more useful than kind.
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

        {inbox !== null && (
          <div className="mt-4 flex gap-1 rounded-lg bg-surface-sunken p-1 text-sm">
            <button
              onClick={() => setTab('write')}
              className={`flex-1 rounded-md px-3 py-1.5 font-medium transition-colors ${
                tab === 'write' ? 'bg-surface text-fg shadow-sm' : 'text-fg-muted hover:text-fg'
              }`}
            >
              Write
            </button>
            <button
              onClick={() => setTab('read')}
              className={`flex-1 rounded-md px-3 py-1.5 font-medium transition-colors ${
                tab === 'read' ? 'bg-surface text-fg shadow-sm' : 'text-fg-muted hover:text-fg'
              }`}
            >
              Inbox ({inbox.length})
            </button>
          </div>
        )}

        {tab === 'read' && inbox !== null ? (
          <div className="mt-5 max-h-[26rem] space-y-3 overflow-y-auto">
            {inbox.length === 0 && (
              <p className="rounded-lg border border-dashed border-line-strong p-6 text-center text-sm text-fg-muted">
                Nothing yet.
              </p>
            )}
            {inbox.map((row) => (
              <div key={row.id} className="rounded-lg border border-line bg-surface-sunken p-3">
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-fg-muted">
                  <span className="rounded bg-surface-sunken px-1.5 py-0.5 font-medium text-fg-muted">
                    {row.category}
                  </span>
                  {row.rating != null && <span>{'★'.repeat(row.rating)}</span>}
                  <span>{row.userEmail || 'anonymous'}</span>
                  <span>·</span>
                  <span>{formatWhen(row.createdAt)}</span>
                  {row.page && <span className="truncate">· {row.page}</span>}
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-sm text-fg">{row.message}</p>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-5">
            {sent ? (
              <div className="rounded-lg border border-green-200 bg-green-50 p-5 text-center">
                <p className="text-sm font-semibold text-green-800">Got it — thank you.</p>
                <p className="mt-1 text-xs text-green-700">Your feedback is saved.</p>
                <button
                  onClick={() => setSent(false)}
                  className="mt-3 text-xs font-medium text-green-800 underline underline-offset-2"
                >
                  Say something else
                </button>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {CATEGORIES.map((c) => (
                    <button
                      key={c.value}
                      onClick={() => setCategory(c.value)}
                      title={c.hint}
                      className={`rounded-lg border px-2 py-2 text-xs font-medium transition-colors ${
                        category === c.value
                          ? 'border-blue-500 bg-blue-50 text-blue-700'
                          : 'border-line bg-surface text-fg-muted hover:border-line-strong'
                      }`}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>

                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={5}
                  maxLength={4000}
                  autoFocus
                  placeholder="What worked, what didn't, what you expected instead…"
                  className="mt-3 w-full resize-y rounded-lg border border-line-strong p-3 text-sm text-fg placeholder:text-fg-subtle focus:border-blue-500 focus:outline-none"
                />

                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-1">
                    <span className="mr-1 text-xs text-fg-muted">Rating (optional)</span>
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        key={n}
                        onClick={() => setRating(rating === n ? null : n)}
                        aria-label={`${n} out of 5`}
                        className={`text-lg leading-none transition-colors ${
                          rating != null && n <= rating ? 'text-amber-500 dark:text-amber-400' : 'text-fg-subtle hover:text-amber-500 dark:hover:text-amber-300'
                        }`}
                      >
                        ★
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={submit}
                    disabled={!message.trim() || sending}
                    className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-fg-inverted transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-surface-raised"
                  >
                    {sending ? 'Sending…' : 'Send feedback'}
                  </button>
                </div>

                {error && (
                  <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                    {error}
                  </p>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
