import React, { useState } from 'react';
import { NoteImportForm } from './NoteImportForm';
import { RecommendationReview } from './RecommendationReview';
import {
  analyzeStream,
  apiApplyRecommendation,
  apiApplyBatch,
  apiUndoBatch,
  apiUpdateRecommendationColumn,
  type AnalyzeInput,
  type Recommendation,
  type ApplyRecommendationBody,
  type KeptCard,
} from '../../../api/noteImports';

// Orchestrates one import: the form, the streaming analysis, and the review/apply loop.
// It owns all session state; the child surfaces are presentational. The board is never mutated from
// here except through the apply endpoints, each of which is the single deterministic write the agent
// is allowed. After any card is created we call `onBoardChanged` so the board behind the modal
// refetches — picking up the new cards *and* the bumped `version`, which keeps the next ordinary
// board edit from being rejected as stale.

interface Member { id: string; name: string; initials: string; color: string; }
interface Column { id: string; title: string; }

type Phase = 'form' | 'results';

export const NoteImportModal = ({
  boardId,
  boardVersion,
  members,
  columns,
  onClose,
  onBoardChanged,
  sampleNote,
}: {
  boardId: string;
  boardVersion: number | null;
  members: Member[];
  columns: Column[];
  onClose: () => void;
  onBoardChanged: () => void;
  sampleNote?: string | null;
}) => {
  const [phase, setPhase] = useState<Phase>('form');
  const [inputError, setInputError] = useState<string | null>(null);

  // The session id arrives on the stream's first event; every apply is scoped to it.
  const [sessionId, setSessionId] = useState<string>('');
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [partialFailureCount, setPartialFailureCount] = useState(0);
  const [streaming, setStreaming] = useState(false);
  const [streamError, setStreamError] = useState<{ code: string; message?: string; action?: string } | null>(null);

  const [batch, setBatch] = useState<{ batchId: string; createdCount: number; recIds: string[]; kept: KeptCard[] } | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const updateRec = (id: string, patch: Partial<Recommendation>) =>
    setRecommendations((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));

  const handleAnalyze = async (input: AnalyzeInput) => {
    setPhase('results');
    setInputError(null);
    setRecommendations([]);
    setWarnings([]);
    setPartialFailureCount(0);
    setStreamError(null);
    setBatch(null);
    setStreaming(true);
    try {
      await analyzeStream(boardId, input, (event) => {
        if (event.type === 'session') {
          setSessionId(event.session.id);
          setWarnings(event.session.warnings ?? []);
        } else if (event.type === 'recommendation') {
          setRecommendations((prev) => [...prev, event.recommendation]);
        } else if (event.type === 'done') {
          setPartialFailureCount(event.partialFailureCount);
        } else if (event.type === 'error') {
          setStreamError({ code: event.code, message: event.message, action: event.action });
        }
      });
    } catch (e: any) {
      // A pre-stream input rejection (bad date, oversized note, no destination column). No cards
      // arrived, so return to the form with the error rather than showing an empty review.
      setPhase('form');
      setInputError(e?.message || 'Could not analyze the note.');
    } finally {
      setStreaming(false);
    }
  };

  const onApproveOne = async (rec: Recommendation, body: ApplyRecommendationBody) => {
    const result = await apiApplyRecommendation(boardId, sessionId, rec.id, body);
    updateRec(rec.id, { state: 'created', createdCardId: result.createdCardId });
    onBoardChanged();
  };

  const onRejectOne = (rec: Recommendation) => updateRec(rec.id, { state: 'rejected' });

  // Persist a per-card column choice so both the individual approve and the bulk path create
  // the card where the reviewer put it. Optimistic, reverting if the server rejects the column.
  const onColumnChange = async (rec: Recommendation, columnId: string) => {
    const previous = rec.targetColumnId;
    if (columnId === previous) return;
    updateRec(rec.id, { targetColumnId: columnId });
    try {
      await apiUpdateRecommendationColumn(boardId, sessionId, rec.id, columnId);
    } catch (e: any) {
      updateRec(rec.id, { targetColumnId: previous });
      setStreamError({ code: 'COLUMN_UPDATE_FAILED', message: e?.message || 'Could not change the column.' });
    }
  };

  const onApplyBatch = async () => {
    const cleanIds = recommendations.filter((r) => r.state === 'pending' && r.blockedReasons.length === 0).map((r) => r.id);
    if (cleanIds.length === 0) return;
    setBulkBusy(true);
    setStreamError(null);
    try {
      const result = await apiApplyBatch(boardId, sessionId, {
        idempotencyKey: crypto.randomUUID(),
        expectedBoardVersion: boardVersion,
      });
      cleanIds.forEach((id) => updateRec(id, { state: 'created' }));
      setBatch({ batchId: result.batchId, createdCount: result.createdCount, recIds: cleanIds, kept: [] });
      onBoardChanged();
    } catch (e: any) {
      setStreamError({ code: 'APPLY_BATCH_FAILED', message: e?.message || 'Could not add the cards.' });
    } finally {
      setBulkBusy(false);
    }
  };

  const onUndoBatch = async () => {
    if (!batch) return;
    setBulkBusy(true);
    try {
      const result = await apiUndoBatch(boardId, sessionId, batch.batchId);
      const keptIds = new Set(result.kept.map((k) => k.recommendationId));
      // A removed card's recommendation returns to pending; a kept one (the user changed it) stays
      // created and is reported, never silently deleted.
      batch.recIds.forEach((id) => {
        if (!keptIds.has(id)) updateRec(id, { state: 'pending', createdCardId: null });
      });
      setBatch(result.kept.length > 0 ? { ...batch, createdCount: 0, kept: result.kept } : null);
      onBoardChanged();
    } catch (e: any) {
      setStreamError({ code: 'UNDO_FAILED', message: e?.message || 'Could not undo.' });
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[10000] flex items-start justify-center overflow-y-auto bg-black bg-opacity-50 p-4">
      <div className="my-8 w-full max-w-3xl rounded-lg bg-surface shadow-xl">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div>
            <h3 className="text-lg font-semibold text-fg">Import notes → cards</h3>
            <p className="text-xs text-fg-subtle">The agent proposes; you approve. Nothing is added until you say so.</p>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-fg-subtle hover:bg-surface-sunken hover:text-fg-muted" aria-label="Close">
            ✕
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto px-6 py-5">
          {phase === 'form' ? (
            <NoteImportForm
              sampleNote={sampleNote}
              disabled={streaming}
              error={inputError}
              onAnalyze={handleAnalyze}
            />
          ) : (
            <RecommendationReview
              recommendations={recommendations}
              members={members}
              columns={columns}
              warnings={warnings}
              partialFailureCount={partialFailureCount}
              streaming={streaming}
              streamError={streamError}
              batch={batch}
              bulkBusy={bulkBusy}
              onApproveOne={onApproveOne}
              onRejectOne={onRejectOne}
              onColumnChange={onColumnChange}
              onApplyBatch={onApplyBatch}
              onUndoBatch={onUndoBatch}
            />
          )}
        </div>

        {phase === 'results' && !streaming && (
          <div className="flex justify-between border-t border-line px-6 py-3">
            <button onClick={() => setPhase('form')} className="text-sm text-fg-muted hover:text-fg">
              ← Import another note
            </button>
            <button onClick={onClose} className="rounded-md bg-fg px-4 py-1.5 text-sm font-medium text-app hover:opacity-90">
              Done
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
