import React, { useMemo, useRef, useState } from 'react';
import type { Recommendation, ApplyRecommendationBody, ResolvedAssignee } from '../../../api/noteImports';

// One proposed card as the reviewer sees and edits it. The rule the whole feature turns on:
// the model only *recommends*; a card is created only from the exact values a human approves here.
// So every field below is editable, and "Approve & create" stays disabled — with the
// reason shown in words — until each thing the server flagged has an explicit human decision.
//
// Evidence is the exception: it is immutable. It is the model's cited source, and letting a
// reviewer rewrite the quote would defeat the point of showing it.

interface Member {
  id: string;
  name: string;
  initials: string;
  color: string;
}

interface Column {
  id: string;
  title: string;
}

// Display bands, mirroring backend `confidence_band`.
const band = (v: number) => (v >= 0.8 ? 'high' : v >= 0.6 ? 'medium' : 'low');
const bandColor: Record<string, string> = {
  high: 'text-green-700 bg-green-50',
  medium: 'text-amber-700 bg-amber-50',
  low: 'text-red-700 bg-red-50',
};
const priorityColor: Record<string, string> = {
  low: 'bg-gray-100 text-gray-600',
  medium: 'bg-blue-100 text-blue-700',
  high: 'bg-red-100 text-red-700',
};

// A flagged owner (ambiguous or unmatched) needs an explicit decision before the card can be
// created. We track one decision per flagged assignee, keyed by its position so two
// people written the same way stay distinct.
type Decision = { kind: 'pending' } | { kind: 'dismissed' } | { kind: 'pick'; memberId: string } | { kind: 'create' };

export const RecommendationItem = ({
  rec,
  members,
  columns,
  onApprove,
  onReject,
  onColumnChange,
}: {
  rec: Recommendation;
  members: Member[];
  columns: Column[];
  onApprove: (body: ApplyRecommendationBody) => Promise<void>;
  onReject: () => void;
  onColumnChange: (columnId: string) => void;
}) => {
  const created = rec.state === 'created';
  const memberById = useMemo(() => new Map(members.map((m) => [m.id, m])), [members]);

  // The editable snapshot. Seeded from the proposal; the server creates *these* values, not a
  // re-reading of the model output.
  const [title, setTitle] = useState(rec.title);
  const [description, setDescription] = useState(rec.description);
  const [priority, setPriority] = useState(rec.priority.value);
  const [dueDate, setDueDate] = useState(rec.dueDate?.value ?? '');
  const [targetColumnId, setTargetColumnId] = useState(rec.targetColumnId);

  // Owners already matched to a real member start selected; the reviewer can freely toggle any.
  const [selectedMemberIds, setSelectedMemberIds] = useState<string[]>(
    rec.assignees.filter((a) => a.resolution === 'existing_member' && a.memberId).map((a) => a.memberId as string)
  );

  // Flagged owners (ambiguous / unmatched), each awaiting a decision.
  const flagged = useMemo(
    () => rec.assignees.map((a, i) => ({ a, i })).filter(({ a }) => a.resolution !== 'existing_member'),
    [rec.assignees]
  );
  const [decisions, setDecisions] = useState<Record<number, Decision>>(() =>
    Object.fromEntries(flagged.map(({ i }) => [i, { kind: 'pending' } as Decision]))
  );

  // The model can invent a quote; an excerpt that isn't in the note is the clearest hallucination
  // signal there is, so a card carrying one needs a deliberate override, not a silent pass.
  const evidenceUnlocatable = rec.blockedReasons.some((r) => r.startsWith('Evidence'));
  const [evidenceOverride, setEvidenceOverride] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Stable across retries of the *same* approval, so a lost response can't create a second card.
  const idempotencyKey = useRef(crypto.randomUUID());

  const toggleMember = (id: string) =>
    setSelectedMemberIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const decide = (i: number, d: Decision) => setDecisions((prev) => ({ ...prev, [i]: d }));

  // At most one new member per card — the backend `apply` creates a single `newMemberName`.
  const createNames = flagged.filter(({ i }) => decisions[i]?.kind === 'create').map(({ a }) => a.rawName);
  const pendingOwners = flagged.filter(({ i }) => decisions[i]?.kind === 'pending');

  // Why Approve is disabled, in words. The button and this list are computed from the same state so
  // they cannot disagree.
  const outstanding: string[] = [];
  if (title.trim() === '') outstanding.push('A title is required.');
  for (const { a } of pendingOwners)
    outstanding.push(`Decide what to do about owner "${a.rawName}".`);
  if (createNames.length > 1) outstanding.push('Only one new member can be created per card — dismiss the others.');
  if (evidenceUnlocatable && !evidenceOverride) outstanding.push('Confirm you have reviewed the unlocatable quote.');

  const approve = async () => {
    if (outstanding.length > 0 || busy) return;
    // Members chosen for ambiguous owners fold into the definite set alongside the freely-toggled ones.
    const picked = flagged
      .map(({ i }) => decisions[i])
      .filter((d): d is { kind: 'pick'; memberId: string } => d?.kind === 'pick')
      .map((d) => d.memberId);
    const assigneeMemberIds = Array.from(new Set([...selectedMemberIds, ...picked]));
    setBusy(true);
    setError(null);
    try {
      await onApprove({
        idempotencyKey: idempotencyKey.current,
        title: title.trim(),
        description,
        targetColumnId,
        assigneeMemberIds,
        priority,
        dueDate: dueDate || null,
        memberCreationApproved: createNames.length === 1,
        newMemberName: createNames.length === 1 ? createNames[0] : null,
      });
    } catch (e: any) {
      setError(e?.message || 'Could not create the card.');
    } finally {
      setBusy(false);
    }
  };

  const confBand = band(rec.confidence);

  if (created) {
    return (
      <div className="rounded-lg border border-green-200 bg-green-50 p-4">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-green-800">✓ Added “{rec.title}”</span>
          <span className="text-xs text-green-600">card created</span>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-line bg-surface p-4 shadow-sm">
      {/* Header: confidence + why the model proposed it (advisory only — never gates approval) */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="flex-1 rounded-md border border-transparent px-1 py-0.5 text-base font-semibold text-fg hover:border-line focus:border-blue-500 focus:outline-none"
        />
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${bandColor[confBand]}`}>
          {confBand} confidence
        </span>
      </div>

      <p className="mb-3 text-xs italic text-fg-subtle">{rec.reason}</p>

      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={2}
        placeholder="No description"
        className="mb-3 w-full rounded-md border border-line p-2 text-sm text-fg-muted focus:border-blue-500 focus:outline-none"
      />

      {/* Column · priority · due date */}
      <div className="mb-3 flex flex-wrap items-end gap-4">
        <label className="text-xs text-fg-muted">
          <span className="mb-1 block font-medium">Add to column</span>
          <select
            value={targetColumnId}
            onChange={(e) => {
              setTargetColumnId(e.target.value);
              onColumnChange(e.target.value); // persist so the bulk path honors it too
            }}
            className="rounded-md border border-line-strong p-1.5 text-sm text-fg"
          >
            {columns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        </label>

        <div className="text-xs text-fg-muted">
          <span className="mb-1 block font-medium">Priority</span>
          <div className="flex gap-1">
            {(['low', 'medium', 'high'] as const).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPriority(p)}
                className={`rounded-md px-2 py-1 text-xs font-medium capitalize ${
                  priority === p ? priorityColor[p] + ' ring-1 ring-inset ring-current' : 'bg-surface-sunken text-fg-subtle'
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </div>

        <label className="text-xs text-fg-muted">
          <span className="mb-1 block font-medium">Due date</span>
          <input
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
            className="rounded-md border border-line-strong p-1.5 text-sm text-fg"
          />
          {/* Show the model's arithmetic against the source phrase so a human can check it. */}
          {rec.dueDate?.rawText && (
            <span className="mt-1 block text-[11px] text-fg-subtle">
              from “{rec.dueDate.rawText}” ({rec.dueDate.provenance})
            </span>
          )}
        </label>
      </div>

      {/* Owners */}
      <div className="mb-3">
        <span className="mb-1 block text-xs font-medium text-fg-muted">Owners</span>
        <div className="flex flex-wrap gap-1.5">
          {members.map((m) => {
            const on = selectedMemberIds.includes(m.id);
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => toggleMember(m.id)}
                className={`flex items-center gap-1 rounded-full border px-2 py-1 text-xs ${
                  on ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-line bg-surface text-fg-muted'
                }`}
              >
                <span className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold text-fg-inverted ${m.color}`}>
                  {m.initials}
                </span>
                {m.name}
              </button>
            );
          })}
          {members.length === 0 && <span className="text-xs text-fg-subtle">This board has no members yet.</span>}
        </div>

        {/* Flagged owners needing a decision */}
        {flagged.map(({ a, i }) => (
          <FlaggedOwner
            key={i}
            assignee={a}
            decision={decisions[i]}
            memberById={memberById}
            createDisabled={createNames.length >= 1 && decisions[i]?.kind !== 'create'}
            onDecide={(d) => decide(i, d)}
          />
        ))}
      </div>

      {/* Evidence — immutable */}
      <div className="mb-3 rounded-md bg-surface-sunken p-2">
        <span className="mb-1 block text-xs font-medium text-fg-muted">Evidence from the note</span>
        <ul className="space-y-1">
          {rec.evidence.map((e, idx) => (
            <li key={idx} className="text-xs text-fg-muted">
              <span className="italic">“{e.excerpt}”</span>
              <span className="ml-1 text-fg-subtle">[{e.locator}]</span>
            </li>
          ))}
        </ul>
        {evidenceUnlocatable && (
          <label className="mt-2 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">
            <input type="checkbox" checked={evidenceOverride} onChange={(e) => setEvidenceOverride(e.target.checked)} className="mt-0.5" />
            <span>A quote above was not found in the note — a possible fabrication. Create only if you have verified it.</span>
          </label>
        )}
      </div>

      {rec.ambiguities.length > 0 && (
        <ul className="mb-3 list-inside list-disc text-xs text-amber-700">
          {rec.ambiguities.map((a, idx) => (
            <li key={idx}>{a}</li>
          ))}
        </ul>
      )}

      {/* Blockers, then actions */}
      {outstanding.length > 0 && (
        <ul className="mb-2 list-inside list-disc text-xs text-fg-muted">
          {outstanding.map((o, idx) => (
            <li key={idx}>{o}</li>
          ))}
        </ul>
      )}
      {error && <p className="mb-2 text-xs text-red-600">{error}</p>}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onReject}
          className="rounded-md px-3 py-1.5 text-sm text-fg-muted hover:bg-surface-sunken"
        >
          Dismiss
        </button>
        <button
          type="button"
          onClick={approve}
          disabled={outstanding.length > 0 || busy}
          className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-semibold text-fg-inverted hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-surface-raised disabled:text-fg-subtle"
        >
          {busy ? 'Creating…' : 'Approve & create'}
        </button>
      </div>
    </div>
  );
};

// One flagged owner's decision control. Ambiguous → pick among the candidates; unmatched → create a
// board member (a separate, explicit approval) or leave the card unassigned.
const FlaggedOwner = ({
  assignee,
  decision,
  memberById,
  createDisabled,
  onDecide,
}: {
  assignee: ResolvedAssignee;
  decision: Decision;
  memberById: Map<string, Member>;
  createDisabled: boolean;
  onDecide: (d: Decision) => void;
}) => {
  const resolved = decision && decision.kind !== 'pending';
  // Suggestions exist only for an unmatched name. `?? []` guards against a backend that omits
  // `candidateMemberIds` — the field would be undefined and `.map` would throw, blanking the review
  // surface.
  const suggestions =
    assignee.resolution === 'unmatched' ? assignee.candidateMemberIds ?? [] : [];
  const chip = (label: string, active: boolean, onClick: () => void, disabled = false) => (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md border px-2 py-1 text-xs ${
        active ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-line bg-surface text-fg-muted'
      } disabled:cursor-not-allowed disabled:opacity-40`}
    >
      {label}
    </button>
  );

  return (
    <div className={`mt-2 rounded-md border p-2 ${resolved ? 'border-line bg-surface-sunken' : 'border-amber-300 bg-amber-50'}`}>
      <p className="mb-1 text-xs text-fg-muted">
        {assignee.resolution === 'ambiguous' ? (
          <>Owner “<b>{assignee.rawName}</b>” matches more than one member — pick one:</>
        ) : suggestions.length > 0 ? (
          // Say WHY it is unmatched. "Not on this board" reads as a bug when the person plainly is
          // on the board — the note just referred to them by first name only.
          <>
            No exact match for “<b>{assignee.rawName}</b>” — the note gives only part of the name.
            Did you mean:
          </>
        ) : (
          <>Owner “<b>{assignee.rawName}</b>” is not on this board:</>
        )}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {assignee.resolution === 'ambiguous' &&
          assignee.alternativeMemberIds.map((mid) =>
            chip(
              memberById.get(mid)?.name ?? mid,
              decision?.kind === 'pick' && decision.memberId === mid,
              () => onDecide({ kind: 'pick', memberId: mid })
            )
          )}
        {/* Suggestions for an unmatched name. Same 'pick' decision as the ambiguous path — the
            human confirms; code never resolved it. */}
        {assignee.resolution === 'unmatched' &&
          suggestions.map((mid) =>
            chip(
              memberById.get(mid)?.name ?? mid,
              decision?.kind === 'pick' && decision.memberId === mid,
              () => onDecide({ kind: 'pick', memberId: mid })
            )
          )}
        {assignee.resolution === 'unmatched' &&
          chip(
            `＋ Create “${assignee.rawName}”`,
            decision?.kind === 'create',
            () => onDecide({ kind: 'create' }),
            createDisabled
          )}
        {chip('Leave unassigned', decision?.kind === 'dismissed', () => onDecide({ kind: 'dismissed' }))}
      </div>
      {assignee.matchRationale && <p className="mt-1 text-[11px] text-fg-subtle">{assignee.matchRationale}</p>}
    </div>
  );
};
