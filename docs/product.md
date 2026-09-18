# Product

What Columnist does, for whom, and where its edges are. How to run it is in the
[README](../README.md); why it is built the way it is, in [decisions.md](decisions.md).

Columnist is a kanban board for small teams with two AI features: an **assistant** that briefs you
on the state of your boards, and **notes to cards**, which turns a meeting note into proposed cards
you approve. Both are optional: without a connected AI model the boards work fully, and the two AI
features say they are switched off.

## The assistant

**The job.** When you are preparing for a management discussion, tell me what matters on my boards,
why, and where it sits, so I can brief others without reading every card.

**What it answers.** Overdue and upcoming work, high-priority and blocked cards, who has the most on
their plate, recent completions, what is in a given column, and follow-ups such as "and which of
those are Priya's?".

**How it answers.**
- The bottom line first, in a sentence or two, then the few cards that matter most.
- Every card is named by where it lives: *Board → Column → Card* across a workspace, *Column →
  Card* inside a board, with priority, due date and assignee inline.
- A recorded fact (a due date, an owner), a derived figure (a count, a comparison) and a judgement
  ("this looks like it needs attention") are worded differently, so you can tell which is which.
- Every specific claim comes from the data. When the data cannot answer, it says so instead of
  answering a nearby question.

**Scope follows where you are.** On a board, answers are limited to that board. On a workspace, to
the boards in that workspace you can access. The app, not the model, decides the scope. When you
move between a board and a workspace, the panel says so and starts a fresh conversation; within one
place it remembers the last few exchanges.

**It never changes anything.** It has no way to create, edit, move, complete or delete a card.

**Out of scope.** Forecasting delivery, ranking people's performance, explaining causes the data
does not record, trends over time, questions about anything other than your boards, and memory
across sessions.

## Notes to cards

**The job.** When a meeting ends and my notes contain commitments, turn them into accurate cards on
the board I am using, while I stay in control of what gets created.

**The flow.**
1. On a board, choose **Import notes** and give it one note: pasted text, a `.txt` file or a
   `.docx` file.
2. Confirm the meeting date (relative dates such as "by Friday" are measured from it) and the
   column new cards should land in.
3. Proposed cards stream in as they are found. Each shows its title, description, owner, due date
   and priority, the sentence from the note it came from, and why it was proposed.
4. Proposals with a problem, such as an owner who matches no one or several people, are flagged
   and must be resolved individually. The rest can be added together with **Add the clean ones**,
   which can be undone while the import is open.
5. Approving creates exactly that card, once. Retrying a request never creates a duplicate.

**What it will and won't infer.** Owners are matched to the board's members; an unmatched owner
can be added as a member only when you approve it, or the card stays unassigned. Due dates are
resolved from the meeting date. Priority is taken from the note when the note supports one, and
otherwise defaults to medium, labelled as a default.

**Out of scope.** It only proposes new cards: it does not update, complete or deduplicate existing
ones, and does not design columns or boards. One note per import. No PDF, `.doc`, images or scans.
Notes over 1 MB, or too long to analyze in one request, are refused with a message rather than
read in part.

**What is kept.** The uploaded file and the note's full text are not stored. The proposal, the
short excerpts that support it, and a fingerprint of the input are, so an import can be reopened
after a refresh. The note's text *is* sent to whichever model provider you configure, along with
the board's member names, so the extraction can resolve owners.

## Accounts and tenancy

Accounts are off when running locally: there is no sign-in and everything is open. The server
refuses to start that way unless `ALLOW_INSECURE_NO_AUTH=true` is set, so an open instance is always
a deliberate choice. With Amazon Cognito configured, every application route requires a session. What stays open by
design: the health check, the sign-in and session endpoints a signed-out browser needs, and the API
documentation (`/docs`, `/redoc`, `/openapi.json`) — see [SECURITY.md](../SECURITY.md).

**Roles.**

| Role | Who | Can |
|---|---|---|
| Operator | Whoever runs the deployment | Create an Instance for a new user, take an Instance down with all its data. Works through a command-line tool, not the app |
| PIU (primary invited user) | The person an Instance is created for | Everything in the Instance, plus invite and remove SIUs, and read the Instance's feedback |
| SIU (secondary invited user) | Someone a PIU invited | Everything in the Instance except inviting or removing people |

**Instances.** An Instance is one private space: its own users, workspaces, boards and cards, fully
isolated from every other Instance. Removing a PIU means taking down their Instance, so an Instance
is never left without an owner.

**Limits** (configurable): 10 Instances per deployment, one PIU per Instance, up to 5 SIUs per PIU,
and a per-user daily allowance of AI tokens.

**Sign-in.** Invite-only email and password. An invited user receives a temporary password and
sets their own on first sign-in; forgotten passwords are reset with an emailed code. Sessions last
12 hours. Tokens are held by the server in cookies the browser's JavaScript cannot read.

**Card assignees are not accounts.** They are names on a board, so a board can list people who
never sign in.

**Audit.** Five events are logged per user: an invited account's first activation, a signed-in
user refused for not having been invited, board saves, member invitations and member removals.
Ordinary sign-ins are not logged.

## Known limits

- Board edits are saved as whole-board snapshots guarded by a version number. A write based on an
  out-of-date board is refused rather than allowed to erase newer changes.
- Team names must be unique across the whole deployment, even though a team itself belongs to one
  Instance.
- In a board, looking up a specific card is checked against every board you can access, not only
  the one you are on.
- A session cannot be refreshed; it ends after 12 hours.
- Quality depends on the model. Results measured on one model say nothing about another; see
  [`agent_test_suite/`](../agent_test_suite/).
