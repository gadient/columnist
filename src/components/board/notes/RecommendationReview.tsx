import React, { useEffect, useState } from 'react';
import { RecommendationItem } from './RecommendationItem';
import type { Recommendation, ApplyRecommendationBody, KeptCard } from '../../../api/noteImports';

// The review surface. It splits the proposal into two piles the way the backend does:
// "clean" is every recommendation no server-side check faulted (empty `blockedReasons`) — those can
// be created in one click; "needs review" is everything with a stated blocker, handled one
// at a time. The split is the server's, re-derived here only for display: the bulk endpoint chooses
// the unflagged set itself, so a flagged card can never ride along.

interface Member { id: string; name: string; initials: string; color: string; }
interface Column { id: string; title: string; }

export const RecommendationReview = ({
  recommendations,
  members,
  columns,
  warnings,
  partialFailureCount,
  streaming,
  streamError,
  batch,
  bulkBusy,
  onApproveOne,
  onRejectOne,
  onColumnChange,
  onApplyBatch,
  onUndoBatch,
}: {
  recommendations: Recommendation[];
  members: Member[];
  columns: Column[];
  warnings: string[];
  partialFailureCount: number;
  streaming: boolean;
  streamError: { code: string; message?: string; action?: string } | null;
  batch: { batchId: string; createdCount: number; kept: KeptCard[] } | null;
  bulkBusy: boolean;
  onApproveOne: (rec: Recommendation, body: ApplyRecommendationBody) => Promise<void>;
  onRejectOne: (rec: Recommendation) => void;
  onColumnChange: (rec: Recommendation, columnId: string) => void;
  onApplyBatch: () => void;
  onUndoBatch: () => void;
}) => {
  // Elapsed seconds during analysis. The shipped `tool` strategy is silent for most of the run and
  // then delivers every card in one burst at the end, so a pulsing dot on its own reads
  // as hung. A ticking counter is the cheapest honest signal that work is still happening.
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!streaming) {
      setElapsed(0);
      return;
    }
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [streaming]);

  const pending = recommendations.filter((r) => r.state === 'pending');
  const clean = pending.filter((r) => r.blockedReasons.length === 0);
  const flagged = pending.filter((r) => r.blockedReasons.length > 0);
  const createdCount = recommendations.filter((r) => r.state === 'created').length;

  return (
    <div className="space-y-4">
      {/* Analysis progress. Keyed on whether any card has actually arrived rather than on the
          configured strategy, so the copy is true either way: under the shipped `tool`
          path nothing arrives until the end, and "0 cards so far" promised a trickle that was
          never coming. Silence gets a duration expectation instead; the count appears only once
          there is something real to count — which is also correct if `json_schema` (which does
          stream progressively) is ever switched on. */}
      {streaming && (
        <div className="flex items-center gap-2 rounded-md bg-blue-50 p-3 text-sm text-blue-700">
          <span className="inline-block h-3 w-3 animate-pulse rounded-full bg-blue-500" />
          {recommendations.length === 0 ? (
            <span>
              Analyzing your note…{' '}
              <span className="text-blue-600/70">
                this usually takes 20–30 seconds{elapsed >= 5 && ` · ${elapsed}s`}
              </span>
            </span>
          ) : (
            <span>
              {recommendations.length} card{recommendations.length === 1 ? '' : 's'} found so far
              {elapsed > 0 && <span className="text-blue-600/70"> · {elapsed}s</span>}
            </span>
          )}
        </div>
      )}

      {/* Things present in the source but not analyzed — images, text boxes */}
      {warnings.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
          <p className="mb-1 font-medium">Not analyzed:</p>
          <ul className="list-inside list-disc">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}

      {/* Recommendations the model returned that we could not read — surfaced, not dropped */}
      {partialFailureCount > 0 && (
        <div className="rounded-md border border-orange-200 bg-orange-50 p-3 text-xs text-orange-800">
          {partialFailureCount} recommendation{partialFailureCount === 1 ? '' : 's'} could not be read and
          {partialFailureCount === 1 ? ' was' : ' were'} skipped. Re-analyze if you expected more.
        </div>
      )}

      {/* A model failure mid-stream arrives in-band; what streamed before it is still here */}
      {streamError && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {streamError.message || 'The analysis did not finish.'}
          {streamError.action && <span className="mt-1 block text-xs">{streamError.action}</span>}
        </div>
      )}

      {/* Undo banner — cheap approval is only safe when retraction is equally cheap */}
      {batch && (
        <div className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          <div className="flex items-center justify-between">
            <span>Added {batch.createdCount} card{batch.createdCount === 1 ? '' : 's'} to the board.</span>
            <button onClick={onUndoBatch} disabled={bulkBusy} className="text-sm font-medium text-green-700 underline hover:text-green-900 disabled:opacity-50">
              Undo
            </button>
          </div>
          {batch.kept.length > 0 && (
            <p className="mt-1 text-xs text-green-700">
              {batch.kept.length} card{batch.kept.length === 1 ? '' : 's'} you had changed {batch.kept.length === 1 ? 'was' : 'were'} kept, not removed.
            </p>
          )}
        </div>
      )}

      {/* Empty is a legitimate answer for a note with no action items — say so plainly */}
      {!streaming && pending.length === 0 && createdCount === 0 && !streamError && (
        <div className="rounded-md border border-line bg-surface-sunken p-6 text-center text-sm text-fg-muted">
          No action items were found in this note.
        </div>
      )}

      {/* Clean pile + the one-click bulk create */}
      {clean.length > 0 && (
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-sm font-semibold text-fg-muted">Ready to add ({clean.length})</h4>
            <button
              onClick={onApplyBatch}
              disabled={streaming || bulkBusy}
              className="rounded-md bg-green-600 px-4 py-1.5 text-sm font-semibold text-fg-inverted hover:bg-green-700 disabled:cursor-not-allowed disabled:bg-surface-raised disabled:text-fg-subtle"
            >
              {bulkBusy ? 'Adding…' : `Add the clean ones (${clean.length})`}
            </button>
          </div>
          <div className="space-y-3">
            {clean.map((rec) => (
              <RecommendationItem
                key={rec.id}
                rec={rec}
                members={members}
                columns={columns}
                onApprove={(body) => onApproveOne(rec, body)}
                onReject={() => onRejectOne(rec)}
                onColumnChange={(colId) => onColumnChange(rec, colId)}
              />
            ))}
          </div>
        </section>
      )}

      {/* Flagged pile — one decision at a time */}
      {flagged.length > 0 && (
        <section>
          <h4 className="mb-2 text-sm font-semibold text-fg-muted">Needs your review ({flagged.length})</h4>
          <div className="space-y-3">
            {flagged.map((rec) => (
              <RecommendationItem
                key={rec.id}
                rec={rec}
                members={members}
                columns={columns}
                onApprove={(body) => onApproveOne(rec, body)}
                onReject={() => onRejectOne(rec)}
                onColumnChange={(colId) => onColumnChange(rec, colId)}
              />
            ))}
          </div>
        </section>
      )}

      {/* Already-created cards, kept visible so the tally is honest */}
      {createdCount > 0 && (
        <section>
          <h4 className="mb-2 text-sm font-semibold text-fg-muted">Added ({createdCount})</h4>
          <div className="space-y-3">
            {recommendations
              .filter((r) => r.state === 'created')
              .map((rec) => (
                <RecommendationItem key={rec.id} rec={rec} members={members} columns={columns} onApprove={async () => {}} onReject={() => {}} onColumnChange={() => {}} />
              ))}
          </div>
        </section>
      )}
    </div>
  );
};
