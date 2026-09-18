// Sample meeting notes for the DEMO WORKSPACE ONLY.
//
// Purpose: give someone something to paste on their first import, so the feature can be tried in
// one click instead of requiring them to go find a real meeting note.
//
// These are AUTHORED, not generated, and deliberately not synthesised from the board's existing
// cards. A note derived from cards already on the board would propose duplicates of them — the
// feature's debut act would be creating redundant work. Instead each note proposes plausible NEW
// work for that board's domain, and names that board's REAL members so owner matching visibly
// succeeds.
//
// The notes collectively exercise the behaviours worth seeing on a first import. Every note carries
// the first three; the fourth appears in some of them, the rest naming an explicit day instead:
//   1. a clean card    — full name owner, concrete deliverable, explicit date → applies in one click
//   2. a first name    — owner named partially → unmatched, with a "Did you mean …?" suggestion
//   3. a vague line    — no owner, no real deliverable → the extractor should propose nothing for it
//   4. a relative date — "by Friday" → deterministic date resolution
//
// Keyed by board title. A board with no entry simply shows no sample-note button, which is what
// keeps this scoped without a feature flag.

export const DEMO_NOTES: Record<string, string> = {
  Engineering: `Sprint planning — engineering

Alex Kim will write the migration plan for the orders table before the next
sprint review, including a rollback path. Target is the 30th.

Priya has to take another look at the retry logic in the webhook consumer —
we saw duplicate deliveries again on Tuesday. Needs doing by Friday.

Marcus Chen is going to benchmark the new caching layer and write up whether
it actually helps p99 latency.

Someone should probably look into the deploy situation at some point.

Note: do not schedule the load test during the release freeze.`,

  'Engineering × PM': `Feature triage — eng + PM

Taylor Reyes to write the one-page spec for saved filters, covering the empty
state and the shared-link behaviour, ready for review by Friday.

Morgan is picking up the accessibility audit of the settings screens — keyboard
navigation and focus order specifically.

Alex Kim will investigate why the CSV export times out on large accounts and
report back with a root cause before we commit to a fix.

We should improve onboarding.`,

  'PM × Sales': `Pipeline sync — PM + sales

Sam Wells is going to put together the competitive comparison for the Q3 deals,
focused on the three objections we keep hearing on pricing. Due the 28th.

Olivia will draft the customer-facing note about the new usage limits, and run
it past legal before anything goes out.

Diego Torres to collect the integration requests from the last ten calls and
rank them by deal value.

Let's circle back on the enterprise stuff.`,

  'PM × Legal': `Contract review sync

Chloe Martin will rewrite the data-retention clause in the standard MSA so it
covers the new EU region, and have it back with us by the 27th.

James is reviewing the sub-processor list against what we actually run in
production — we think two entries are stale.

Rina Patel to summarise what the updated DPA means for customers already on
annual contracts, in plain language we can send out.

We need to sort out the compliance situation.

Reminder: nothing here is legal advice until Chloe has signed off.`,

  'PM × Marketing': `Launch planning — product + marketing

Nina Fox will write the positioning brief for the reporting release, including
the three headline claims and what we can prove, due by Thursday.

Carlos is drafting the launch-day email sequence and wants the feature list
locked before he starts.

Aisha Diallo to pull the engagement numbers from the last two launches so we
can set a realistic target for this one.

Let's do something bigger this time.`,

  'PM × PM': `Cross-team planning

Taylor Reyes will consolidate the three roadmap drafts into one sequenced plan
for the quarter, flagging where two teams have committed to the same week.
Wants it circulated by the 26th.

Morgan is going to write up the intake process for mid-quarter requests, since
we keep relitigating it.

Sam Wells to audit which OKRs from last quarter never got a named owner.

We should think about how we prioritise.

Not a task: the offsite agenda is already covered.`,

  'Sales Pipeline': `Pipeline review

Jordan Pierce will rebuild the forecast model with the new close-rate
assumptions and walk the team through it by Friday.

Sofia is taking the security questionnaire for the healthcare deal — she needs
the SOC 2 evidence pack from us first.

Rafael Stone to write the mutual action plan template we can attach to every
proposal over 50k.

Someone needs to own the churn conversation.`,

  'Marketing Campaign': `Campaign standup

Nina Fox will finalise the creative brief for the spring campaign, including
channel split and the two audience segments we agreed, by the 29th.

Carlos is rewriting the landing page copy against the new positioning — first
pass by Wednesday.

Mia Torres to set up the attribution tracking so we can actually tell which
channel drove signups this time.

The brand stuff needs a rethink.`,

  'Customer Success': `Account review

Lena Park will build the health-score dashboard covering usage, ticket volume
and last executive touch, ready for the 30th.

Omar is preparing the renewal case for the two at-risk enterprise accounts,
with the usage evidence attached.

Bea Collins to write the onboarding checklist for self-serve customers, since
the current one assumes a dedicated CSM.

We should be more proactive with customers.

FYI: do not contact the accounts already in the escalation queue.`,

  'OKR & Strategy': `Quarterly planning

Alex Kim will write the engineering capacity model for next quarter so we stop
committing to more than we can staff. Needed by the 28th.

Taylor is drafting the one-page strategy summary that goes to the board, and
wants the metrics section reviewed before it ships.

Sofia Nguyen to map each proposed initiative to the revenue goal it actually
serves, and call out the ones that map to nothing.

We need better alignment across the org.`,
};

/** The demo workspace's name — the same string `loadDemoData` creates and finds by. */
export const DEMO_WORKSPACE_NAME = 'Demo Workspace';

/**
 * A sample note for this board, or null.
 *
 * Gated on BOTH the workspace name and a board-title match, deliberately. A user who happens to
 * name their own workspace "Demo Workspace" still sees nothing, because their board titles will
 * not match the authored set — so a real workspace can never sprout a "load sample text" button.
 * Two conditions, and no schema change.
 */
export function getDemoNote(workspaceName: string, boardTitle: string): string | null {
  if (workspaceName !== DEMO_WORKSPACE_NAME) return null;
  return DEMO_NOTES[boardTitle] ?? null;
}
