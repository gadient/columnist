# Decisions

The forks that shaped Columnist: what was chosen, what was turned down, and why. Only decisions
with a real alternative are here; the rest follows from these.

## The model proposes; deterministic code writes

**Chosen.** The notes-to-cards model returns a proposal against a schema that has no field for
anything it may not decide. Server code validates it, and creates a card only after a person
approves that exact proposal.

**Turned down.** Letting the model create cards, with a confidence threshold deciding which.

**Why.** A confidence score is the model's own estimate of its output, so using it as the write
condition puts the failure and its detector in the same place. An approval step does not depend on
the model being right about itself.

The proposal schema is narrow on purpose. Its only action is "create a card", and it has no field
for a card ID, a member ID or an approval, so an instruction smuggled in through a note ("update
card 5", "mark this approved") cannot even be expressed, let alone carried out. Extra fields are
rejected rather than ignored. Matching names to board members, checking that each quoted excerpt
really appears in the note, and deciding which proposals are clean are all done by code afterwards.

## Guardrails are structural first, prompt second

**Chosen.** Every chat tool is a read-only query. The board being viewed is supplied by the server
and appears in no tool schema. Every query is filtered below the model against the caller's allowed
boards. The system prompt covers only what structure cannot: treating card text as data, and not
answering a nearby question instead of the one asked.

**Turned down.** Instructing the model to stay within the user's data, and filtering questions by
keyword before they reach it.

**Why.** A prompt constrains the model only while the model follows it; a filter below the model
holds even when it doesn't. A keyword filter over natural language rejects good questions and
misses bad ones.

## Every bound fails closed

**Chosen.** The assistant runs under a limit on turns, tokens per request and wall-clock time. Hitting
any of them, a reply cut off at the output limit, or a provider error, returns a plain statement
that it could not finish, never the partial text.

**Turned down.** Returning whatever the model had written when it was stopped.

**Why.** A truncated answer reads like a complete one. The user cannot tell the difference, so the
system has to.

## No answers without a model

**Chosen.** With no AI model connected, the assistant and notes to cards say they are switched off.

**Turned down.** A keyword-matching fallback that answers common questions without a model.

**Why.** A fallback that answers some questions plausibly and others wrongly is worse than an
honest refusal, because the user cannot tell which answers to trust.

## Language to the model, arithmetic to code

**Chosen.** The model reads what a note means; a date resolver computes what "by Friday" means
against the meeting date, and overrides the model's date when the phrase is unambiguous.

**Turned down.** Trusting the model's date arithmetic.

**Why.** Reading intent is what language models are good at; weekday arithmetic is not, and it was
wrong often enough to matter.

## Whole-board writes, guarded by a server version

**Chosen.** The client saves a board as a whole snapshot, stamped with the version it was based on.
The server owns the version, increments it on every change, and refuses a stale snapshot.

**Turned down.** Guarding on the board's last-updated timestamp; rewriting the write path as
per-action endpoints.

**Why.** Imported cards are created on the server while the board is open in the browser, so an
unguarded snapshot would silently delete them. A timestamp cannot guard this: it has one-second
resolution and the client supplies it. Per-action endpoints would also work, but would rewrite the
path every board interaction goes through.

## Applying a card is one transaction with its own record

**Chosen.** Creating an approved card, any member it needs, and the record that makes a retry
harmless happen in a single transaction.

**Turned down.** Composing the existing store helpers, which each commit on their own, and cleaning
up after a failure.

**Why.** Cleanup handles a failure but not a crash between two commits, and a crash there means a
retry creates a second card.

## A private Instance per invited user

**Chosen.** Each invited user gets an isolated Instance and invites their own collaborators into
it. Accounts are created only by invitation.

**Turned down.** One shared tenant with per-workspace sharing; open sign-up.

**Why.** Full isolation makes "remove this user and everything they own" a single, well-defined
delete, and makes it impossible for one group to see another's boards through a sharing mistake.

## Tokens stay on the server

**Chosen.** A custom sign-in form posts to the backend, which talks to Cognito and sets the session
in cookies that JavaScript cannot read. State-changing requests also require a CSRF token.

**Turned down.** Cognito's hosted sign-in page, and storing tokens in the browser.

**Why.** A token JavaScript cannot read cannot be stolen by a script injected into the page.

## One setting, three model providers

**Chosen.** `AGENT_BACKEND` selects Amazon Bedrock, the OpenAI API or the Anthropic API for both
agents. The chat loop keeps a single message format, and each provider translates at the edge.

**Turned down.** Bedrock only.

**Why.** Running the agents should not require an AWS account. Keeping the translation at the edge
leaves the loop, the tools and the access filter identical on every provider.

## A quality bar set before choosing a model

**Chosen.** Extraction had to reach 90% precision and 80% recall on labelled notes before a model
was accepted. The labels and the scorer ship with the code.

**Turned down.** Choosing a model by trying it on a few notes.

**Why.** A bar written down in advance cannot move to fit whichever model looked good, and anyone
can re-run it against the model they configure.
