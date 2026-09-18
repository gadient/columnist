import React, { useState, useEffect, useRef } from 'react';
import { Sparkles, Send, ChevronRight } from 'lucide-react';
import { apiRequest } from '../../api/client';
import { ChatMessageContent } from './ChatMessageContent';
import { HowChatWorks } from '../onboarding/HowChatWorks';

// How many prior exchanges to replay. Matches the backend cap (`AgentChatInput.history`,
// max_length=6): a short window is enough to resolve "them"/"there" while keeping the tokens the
// history adds — it sits after the cached prefix, so it is never cached — small.
const HISTORY_WINDOW = 6;
const MAX_ANSWER_CHARS = 4000; // the backend's `ChatExchange.answer` cap; truncate rather than 422.

// Distil the visible conversation into the compact {question, answer} pairs the backend replays.
// Only successful exchanges: an error turn (the assistant could not answer) is dropped whole, so a
// failed question never returns as if it had an answer, and alternation stays intact. Newest
// `HISTORY_WINDOW` pairs, oldest first.
function buildHistory(msgs: any[]): Array<{ question: string; answer: string }> {
  const pairs: Array<{ question: string; answer: string }> = [];
  for (let i = 0; i < msgs.length - 1; i++) {
    const q = msgs[i];
    const a = msgs[i + 1];
    if (q.role === 'user' && a?.role === 'assistant' && !a.isError && a.text) {
      pairs.push({ question: q.text.slice(0, 1200), answer: String(a.text).slice(0, MAX_ANSWER_CHARS) });
      i++; // consume the answer we just paired
    }
  }
  return pairs.slice(-HISTORY_WINDOW);
}

// Deterministic, app-generated scope-transition notice. Keyed on the DESTINATION scope,
// so it also covers board→board and workspace→workspace. The
// wording is fixed here, never produced by the model — it is a statement of fact about what the
// assistant can now see, and must read the same every time for the same transition.
function buildTransitionNotice(boardId: string | null, workspaceId: string | null, name?: string | null): string {
  const here = name || (boardId ? 'this board' : 'this workspace');
  if (boardId) return `You are now in ${here}. Answers are limited to this board.`;
  if (workspaceId) return `You are now in ${here}. Answers can use all boards you can access in this workspace.`;
  return 'You can now ask about all your boards.';
}

// `scopeLabel` names what the assistant can currently see. It is not decoration: the same question
// returns different answers on a board (that board only) and in a workspace (every board you can
// access), and without saying so the panel looks like it is ignoring your data when it is simply
// looking somewhere else. The label is supplied by the caller because only the caller knows
// whether it is wrapping one board or a whole workspace. `scopeName` is the bare destination name
// (workspace or board) used for the transition notice — distinct from the phrase-shaped scopeLabel.
export const ChatExtension = ({ activeBoardId, activeWorkspaceId, scopeLabel, scopeName, onClose, onOpenFeedback }) => {
  const scopeText = scopeLabel || (activeBoardId ? 'this board' : 'all your boards');
  // The intro is DERIVED, not seeded into `messages`. The panel mounts before the workspace's
  // boards finish loading, so a count baked into initial state froze at "0 boards" and never
  // recovered once they arrived. Rendering it from the live `scopeText` each time fixes that.
  const [messages, setMessages] = useState<any[]>([]);
  const [draft, setDraft] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  // Notices the backend attached to its last reply. Empty until it has answered once — a banner
  // asserting the assistant is not configured must come from the running backend, not from a
  // string compiled into the bundle.
  const [backendWarnings, setBackendWarnings] = useState<string[]>([]);
  const [showHowChatWorks, setShowHowChatWorks] = useState(false);
  const messagesEndRef = useRef(null);

  // Stale-reply guard. `scopeKey` identifies the scope the panel is currently showing; a request is
  // tagged with the key it was issued under, and a reply is dropped if that key no longer matches
  // by the time it arrives (the user navigated away). `sigRef` remembers the server's scope
  // signature for the active scope — a later reply carrying a different one means the backend
  // resolved a different fence than earlier in this conversation, so we fail closed rather than
  // render it. Refs, not state: the async response handler must read the value at *resolve* time.
  const scopeKey = activeBoardId ? `board:${activeBoardId}` : activeWorkspaceId ? `ws:${activeWorkspaceId}` : 'global';
  const scopeKeyRef = useRef(scopeKey);
  const sigRef = useRef<string | null>(null);
  // Tracks the scope the panel was showing, to tell a real navigation apart from the first mount:
  // the transition notice must fire on the former but not the latter (there is no "from" on mount).
  const prevScopeRef = useRef<string | undefined>(undefined);
  scopeKeyRef.current = scopeKey;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, isTyping]);

  // Conversation memory is partitioned per scope: the buffer belongs to the board or
  // workspace it was built in. Moving to a different scope starts a fresh conversation rather than
  // carrying "them"/"there" across a boundary where they mean something else — the history is only
  // ever the user's own prior answers, so a clean reset is a sufficient partition. Restoring a
  // scope's prior history on return, and cross-scope subject carry, are not implemented.
  useEffect(() => {
    const prev = prevScopeRef.current;
    prevScopeRef.current = scopeKey;
    setBackendWarnings([]);
    setIsTyping(false);   // a spinner from the old scope's in-flight request must not linger here
    sigRef.current = null; // fresh scope: no established signature yet

    // First mount has no prior scope — open clean, no "you are now in…" notice. A real navigation
    // between scopes starts the new (partitioned) conversation with the deterministic notice, so
    // the scope change is acknowledged inline without the user having to start a new chat.
    if (prev === undefined || prev === scopeKey) {
      setMessages([]);
      return;
    }
    setMessages([
      {
        id: `sys_${Date.now()}`,
        role: 'system',
        text: buildTransitionNotice(activeBoardId, activeWorkspaceId, scopeName),
      },
    ]);
    // scopeName is intentionally read at transition time, not a dep: it is already the destination's
    // by the time scopeKey changes.
  }, [scopeKey]);

  // Starter questions, shown only before the first message. These are deliberately the ones the
  // assistant answers WELL (measured by the response-contract eval) and that the demo data can
  // answer — overdue, blocked, workload, a briefing. None is a follow-up: a chip opens the
  // conversation, so there is no earlier answer for it to build on.
  const suggestions = [
    "What's at risk this week?",
    'What needs attention, and why?',
    "What's blocked?",
    'Who has the most on their plate?',
  ];

  const sendMessage = async (explicit?: string) => {
    const text = (explicit ?? draft).trim();
    if (!text || isTyping) return;

    // Snapshot the history from the conversation SO FAR — before this new question is appended —
    // so the pairs the backend replays are the earlier exchanges, not this turn.
    const history = buildHistory(messages);
    // The scope this request is issued under. If it no longer matches when the reply lands, the
    // user has navigated and the reply must not render here.
    const issuedScope = scopeKeyRef.current;

    const userMessage = {
      id: `u_${Date.now()}`,
      role: 'user',
      text
    };

    setMessages(prev => [...prev, userMessage]);
    setDraft('');
    setIsTyping(true);

    try {
      const result = await apiRequest('/agent/chat', {
        method: 'POST',
        body: JSON.stringify({ message: text, activeBoardId, activeWorkspaceId, history })
      });

      // A late reply from a scope the user has left must not render. The scope-change
      // effect already cleared this panel; appending here would paint an old-scope answer into a
      // new-scope conversation.
      if (scopeKeyRef.current !== issuedScope) return;
      // The server's scope signature must stay consistent within one scope. A different one
      // means the backend resolved a different fence than earlier in this conversation; fail closed.
      if (result.scopeSignature) {
        if (sigRef.current && sigRef.current !== result.scopeSignature) return;
        sigRef.current = result.scopeSignature;
      }

      // The backend decides whether a notice applies — it sends the "not configured" warning
      // whenever no model backend is configured. Holding it in state means the banner disappears by
      // itself the moment the real backend answers, instead of contradicting it.
      setBackendWarnings(result.warnings || []);
      setMessages(prev => [
        ...prev,
        {
          id: `a_${Date.now()}`,
          role: 'assistant',
          text: result.response,
          presentation: result.presentation || null,
          warnings: []
        }
      ]);
    } catch (error) {
      // A late failure from a scope the user has left is stale too — drop it.
      if (scopeKeyRef.current !== issuedScope) return;
      // Say what actually failed. `apiRequest` already builds a specific message — a 401, a 429
      // over the daily token cap, a 502 while the service is still starting, or a proxy error page
      // ("HTML instead of JSON") all arrive here distinguishable; a fixed generic string would
      // throw that away and send you after the wrong thing.
      const detail = error instanceof Error ? error.message : String(error);
      setMessages(prev => [
        ...prev,
        {
          id: `a_${Date.now()}`,
          role: 'assistant',
          text: `The assistant could not answer: ${detail}`,
          presentation: null,
          warnings: [],
          // Excluded from replayed history: a failed turn has no real answer to carry forward.
          isError: true
        }
      ]);
    } finally {
      // Only clear the spinner if we are still in the scope that started it. A superseded request
      // resolving after the user navigated must not switch off the new scope's in-flight indicator.
      if (scopeKeyRef.current === issuedScope) setIsTyping(false);
    }
  };

  return (
    <div className="h-full flex flex-col bg-surface-sunken text-fg">
      <div className="px-5 py-4 border-b border-line flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-blue-500 to-purple-500 flex items-center justify-center shadow-sm">
          <Sparkles size={18} className="text-fg-inverted" />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <h3 className="font-bold text-fg text-sm">Assistant</h3>
            <span className="rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-amber-700 dark:text-amber-300">
              Alpha
            </span>
          </div>
          <p className="text-xs text-fg-subtle truncate" title={`Searching ${scopeText}`}>
            <span className="text-fg-muted">Searching</span> {scopeText}
          </p>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="ml-auto w-8 h-8 rounded-lg text-fg-subtle hover:text-fg hover:bg-surface flex items-center justify-center transition-colors"
            title="Collapse panel"
            aria-label="Collapse chat panel"
          >
            <ChevronRight size={18} />
          </button>
        )}
      </div>

      {/* Standing note, not a dismissible toast: this is a work-in-progress feature and the
          expectation needs setting every time the panel is opened, not once. Both links are here
          because someone reading the caveats is exactly who should find the feedback box without
          hunting for it. */}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-amber-500/20 bg-amber-500/5 px-4 py-2 text-[11px] leading-snug text-amber-700 dark:text-amber-200/90">
        <span>Early alpha — it follows this conversation, but starts fresh when you switch board or workspace.</span>
        <button
          onClick={() => setShowHowChatWorks(true)}
          className="font-semibold text-amber-700 dark:text-amber-200 underline underline-offset-2 hover:text-amber-800 dark:hover:text-amber-100"
        >
          How it works
        </button>
        {onOpenFeedback && (
          <>
            <span className="text-amber-500/40">·</span>
            <button
              onClick={onOpenFeedback}
              className="font-semibold text-amber-700 dark:text-amber-200 underline underline-offset-2 hover:text-amber-800 dark:hover:text-amber-100"
            >
              Report a problem
            </button>
          </>
        )}
      </div>

      {showHowChatWorks && <HowChatWorks onClose={() => setShowHowChatWorks(false)} />}

      {backendWarnings.map((warning, idx) => (
        <div
          key={`banner_${idx}`}
          className="px-4 py-2 border-b border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300 text-[11px] leading-snug"
        >
          {warning}
        </div>
      ))}

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        <div className="mr-auto max-w-[90%] px-4 py-3 rounded-2xl text-sm leading-relaxed bg-surface text-fg border border-line">
          Ask about tasks, due dates, priorities, action items, or summaries — across {scopeText}.
        </div>

        {!messages.some((m) => m.role === 'user') && (
          <div className="flex flex-wrap gap-2 pt-1">
            {suggestions.map((q) => (
              <button
                key={q}
                onClick={() => sendMessage(q)}
                className="rounded-full border border-line bg-surface/60 px-3 py-1.5 text-xs text-fg-muted hover:border-blue-500 hover:text-fg transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {messages.map(message => (
          message.role === 'system' ? (
            // App-generated scope-transition notice: centred and muted, visibly not a chat
            // turn — it is the panel telling you where it is now looking, not the assistant talking.
            <div
              key={message.id}
              className="mx-auto max-w-[92%] rounded-lg bg-surface-sunken border border-line px-3 py-2 text-center text-xs text-fg-muted"
            >
              {message.text}
            </div>
          ) : (
            <div
              key={message.id}
              className={`max-w-[90%] px-4 py-3 rounded-2xl text-sm leading-relaxed ${
                message.role === 'user'
                  ? 'ml-auto bg-blue-600 text-fg-inverted'
                  : 'mr-auto bg-surface text-fg border border-line'
              }`}
            >
              <ChatMessageContent message={message} />
            </div>
          )
        ))}
        {isTyping && (
          <div className="mr-auto bg-surface text-fg-subtle border border-line px-4 py-2 rounded-2xl text-sm flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce [animation-delay:-0.3s]" />
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce [animation-delay:-0.15s]" />
            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-bounce" />
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 border-t border-line">
        <div className="bg-surface border border-line rounded-2xl p-2 flex items-end gap-2 focus-within:border-blue-500 transition-colors">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Type a message..."
            rows={1}
            className="flex-1 bg-transparent text-sm text-fg placeholder:text-fg-subtle resize-none outline-none px-2 py-1"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
          />
          <button
            onClick={() => sendMessage()}
            disabled={!draft.trim()}
            className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-purple-500 text-fg-inverted flex items-center justify-center hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            title="Send"
          >
            <Send size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};
