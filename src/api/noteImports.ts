// Client for the Notes-to-Cards agent. The backend is the authority on every decision
// here — this layer only carries an approved snapshot to it and streams its proposals back. It
// never invents a card, a column, or a member id: those are the server's to decide.
import { API_BASE } from './client';

// ── The contract, as it arrives on the wire (mirrors backend `schema.py`) ──────────────────────
// These are what the model *claimed*, already turned into a reviewable proposal by server-side
// resolution. The two-schema split is deliberate: the model has no field for a member id or a
// column, so a hallucinated identity cannot even be expressed. See backend `schema.py`.

export interface Evidence {
  excerpt: string; // verbatim from the note, immutable — user edits never touch it
  locator: string; // "line:5" | "paragraph:12" | "table:1:row:3:cell:2"
}

export type AssigneeResolution = 'existing_member' | 'ambiguous' | 'unmatched';

export interface ResolvedAssignee {
  rawName: string; // the text the model read in the note
  resolution: AssigneeResolution;
  memberId: string | null; // set only when resolution === 'existing_member'
  alternativeMemberIds: string[]; // the candidates when ambiguous
  candidateMemberIds: string[]; // near-misses SUGGESTED for an unmatched name — never auto-applied
  matchRationale: string | null; // why "A. Smith" matched "Alice Smith"
  confidence: number;
}

export type Provenance = 'explicit' | 'inferred' | 'default';

export interface DueDate {
  value: string | null; // ISO YYYY-MM-DD, or null when the note supports no date
  rawText: string | null; // the source phrase, e.g. "by Friday" — shown beside `value`
  provenance: Provenance;
  confidence: number;
}

export interface CardPriority {
  value: 'low' | 'medium' | 'high';
  rawText: string | null;
  provenance: Provenance;
  confidence: number;
}

export interface Recommendation {
  id: string; // server-generated
  action: 'create_card';
  title: string;
  description: string;
  targetColumnId: string; // the destination the user chose, stamped by code
  assignees: ResolvedAssignee[];
  dueDate: DueDate | null;
  priority: CardPriority;
  evidence: Evidence[];
  reason: string;
  confidence: number;
  ambiguities: string[];
  state: 'pending' | 'rejected' | 'created' | 'failed';
  // Why "Approve & create" is disabled, in words. Empty ⇒ approvable. Computed server-side so the
  // button and the server can never disagree.
  blockedReasons: string[];
  // Set when an unmatched owner would require creating a board member — its own separate approval.
  // Never creates a licensed user.
  requiresNewMemberNamed: string | null;
  createdCardId: string | null;
}

export interface NoteImportSession {
  id: string;
  boardId: string;
  boardVersion: number;
  meetingDate: string;
  inputMode: string; // 'paste' | 'txt' | 'docx'
  status: string; // 'ready' | 'failed'
  warnings: string[]; // things present but not analyzed — images, text boxes
  partialFailureCount: number; // recommendations the server could not read
  recommendations: Recommendation[];
}

// ── Streaming analyze ──────────────────────────────────────────────────────────────────────────
// NDJSON, one event per line: `session` first, then a `recommendation` per card as the model
// finishes writing it, then `done` — or an in-band `error` if the model fails mid-stream (the 200
// headers are already sent by then, so a failure can't change the status).

export type StreamEvent =
  | { type: 'session'; session: Omit<NoteImportSession, 'recommendations' | 'partialFailureCount' | 'status'> }
  | { type: 'recommendation'; found: number; recommendation: Recommendation }
  | { type: 'done'; found: number; partialFailureCount: number }
  | { type: 'error'; found: number; code: string; message?: string; action?: string };

export interface AnalyzeInput {
  meetingDate: string; // YYYY-MM-DD — the anchor for relative dates like "by Friday"
  clientRequestId: string; // suppresses accidental duplicate submissions
  // Optional default destination. Omitted here: notes never name a column, so the model has
  // nothing to suggest and the reviewer picks per card. When absent, the backend defaults every card
  // to the board's first column, which the reviewer then re-targets (see apiUpdateRecommendationColumn).
  targetColumnId?: string;
  pastedText?: string; // exactly one of pastedText / file
  file?: File;
  timezone?: string;
}

// The CSRF double-submit header, echoed on unsafe methods (see client.ts). Duplicated here rather
// than exported from client.ts because a streaming fetch can't go through `apiRequest`.
const CSRF_COOKIE = 'columnist_csrf';
const CSRF_HEADER = 'X-CSRF-Token';

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}

function buildForm(input: AnalyzeInput): FormData {
  const form = new FormData();
  form.set('meetingDate', input.meetingDate);
  form.set('clientRequestId', input.clientRequestId);
  if (input.targetColumnId) form.set('targetColumnId', input.targetColumnId);
  if (input.timezone) form.set('timezone', input.timezone);
  // Exactly one source. The backend rejects both-or-neither, but sending only one keeps that
  // contract honest from this side too.
  if (input.file) form.set('file', input.file);
  else form.set('pastedText', input.pastedText ?? '');
  return form;
}

/**
 * Analyze one note, streaming each proposed card back as it is produced.
 *
 * `onEvent` fires once per NDJSON line, in order. Input errors (bad date, oversized note, no
 * destination column) arrive as a thrown Error *before* the stream opens — those are ordinary
 * HTTP failures. A failure *during* generation arrives instead as a trailing `{type:'error'}`
 * event, because the 200 headers are already sent by then.
 *
 * We reach for `fetch` + `ReadableStream` rather than `EventSource` because this is a POST with a
 * multipart body; `EventSource` is GET-only.
 */
export async function analyzeStream(
  boardId: string,
  input: AnalyzeInput,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const csrf = readCookie(CSRF_COOKIE);
  const response = await fetch(`${API_BASE}/boards/${boardId}/note-imports/analyze/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: csrf ? { [CSRF_HEADER]: csrf } : {}, // no Content-Type: the browser sets the multipart boundary
    body: buildForm(input),
    signal,
  });

  if (!response.ok || !response.body) {
    // A pre-stream input rejection. The body is the catalog entry {code, message, action}.
    let message = `Analyze failed (${response.status})`;
    try {
      const payload = await response.json();
      message = payload?.detail?.message || payload?.detail || payload?.error || message;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(message);
  }

  // Read the body as it arrives, splitting on newlines. A line may span two chunks, so we keep a
  // buffer and only parse up to the last complete newline.
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf('\n');
    while (newline !== -1) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as StreamEvent);
      newline = buffer.indexOf('\n');
    }
  }
  const tail = buffer.trim();
  if (tail) onEvent(JSON.parse(tail) as StreamEvent);
}

// ── Non-streaming reads & mutations (plain JSON) ────────────────────────────────────────────────

async function jsonRequest(path: string, init: RequestInit): Promise<any> {
  const method = (init.method || 'GET').toUpperCase();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (method !== 'GET') {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers[CSRF_HEADER] = csrf;
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, credentials: 'include', headers: { ...headers, ...(init.headers as any) } });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      message = payload?.detail?.message || payload?.detail || payload?.error || message;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

/** Restore a proposal after a refresh. */
export const apiGetSession = (boardId: string, sessionId: string): Promise<NoteImportSession> =>
  jsonRequest(`/boards/${boardId}/note-imports/${sessionId}`, { method: 'GET' });

/** Re-target one still-pending recommendation to a different column. Persisted so
 * the bulk "Add the clean ones" path — which reads the stored column — honors the reviewer's choice.
 * Returns the updated recommendation. */
export const apiUpdateRecommendationColumn = (
  boardId: string,
  sessionId: string,
  recommendationId: string,
  targetColumnId: string
): Promise<Recommendation> =>
  jsonRequest(`/boards/${boardId}/note-imports/${sessionId}/recommendations/${recommendationId}`, {
    method: 'PATCH',
    body: JSON.stringify({ targetColumnId }),
  });

// The exact values a human approved for one card. Sent whole, not by id: the user may have
// edited the title, owner, column, or date during review, so the server creates *these* values.
export interface ApplyRecommendationBody {
  idempotencyKey: string; // survives a lost response so a retry can't create a second card
  title: string;
  description?: string;
  targetColumnId: string;
  assigneeMemberIds?: string[]; // existing board members only
  priority?: 'low' | 'medium' | 'high';
  dueDate?: string | null;
  memberCreationApproved?: boolean; // creating a member is a separate, explicit approval
  newMemberName?: string | null;
}

export interface ApplyResult {
  sessionId: string;
  recommendationId: string;
  createdCardId: string | null;
  createdMemberId: string | null;
  state: string;
  replayed: boolean; // true when a retry returned the original card rather than creating one
  boardVersion: number | null; // adopt before the next snapshot write, or it is rejected as stale
}

export const apiApplyRecommendation = (
  boardId: string,
  sessionId: string,
  recommendationId: string,
  body: ApplyRecommendationBody
): Promise<ApplyResult> =>
  jsonRequest(`/boards/${boardId}/note-imports/${sessionId}/recommendations/${recommendationId}/apply`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export interface ApplyBatchResult {
  sessionId: string;
  batchId: string;
  createdCount: number;
  createdCardIds: string[];
  boardVersion: number;
}

/** "Add the clean ones". The server chooses the unflagged set — no list rides along, so a
 * flagged card cannot be smuggled in. */
export const apiApplyBatch = (
  boardId: string,
  sessionId: string,
  body: { idempotencyKey: string; expectedBoardVersion?: number | null }
): Promise<ApplyBatchResult> =>
  jsonRequest(`/boards/${boardId}/note-imports/${sessionId}/apply-batch`, {
    method: 'POST',
    body: JSON.stringify(body),
  });

export interface KeptCard {
  recommendationId: string;
  cardId: string | null;
  reason: string;
}

export interface UndoBatchResult {
  sessionId: string;
  batchId: string;
  removedCount: number;
  keptCount: number;
  kept: KeptCard[]; // cards undo declined to remove because the user had changed them
  boardVersion: number;
}

export const apiUndoBatch = (boardId: string, sessionId: string, batchId: string): Promise<UndoBatchResult> =>
  jsonRequest(`/boards/${boardId}/note-imports/${sessionId}/batches/${batchId}/undo`, { method: 'POST' });
