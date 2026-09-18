import React, { useState, useEffect, useRef, Suspense, lazy } from 'react';
import { DEMO_BOARDS } from './data/demoBoards';
import { resolveCardDueDate } from './data/demoDates';
import {
  apiCreateBoard, apiDeleteBoard, apiSyncBoard, apiToggleCardComplete, apiGetBoard, toBoardMap
} from './api/boards';
import {
  apiListWorkspaces, apiCreateWorkspace, apiRenameWorkspace,
  apiDeleteWorkspace, apiListWorkspaceBoards
} from './api/workspaces';
import { WorkspaceListScreen } from './components/workspace/WorkspaceListScreen';
import { WorkspaceBoardsView } from './components/workspace/WorkspaceBoardsView';
import { KanbanBoard } from './components/board/KanbanBoard';
import { WorkspaceWithChat } from './components/chat/WorkspaceWithChat';
import { AccountMenu } from './components/auth/AccountMenu';
import { ThemeToggle } from './theme/ThemeToggle';
import { FeedbackModal } from './components/feedback/FeedbackModal';
// Lazy so marked + the inlined doc content (and, on the Architecture tab, mermaid) stay out of the
// main bundle until the user opens About.
const AboutModal = lazy(() => import('./components/about/AboutModal').then((m) => ({ default: m.AboutModal })));
import { ErrorBanner, type AppError } from './components/common/ErrorBanner';

export default function MultiBoardApp() {
  // views: 'welcome' | 'workspace' | 'board'
  const [currentView, setCurrentView] = useState('welcome');
  const [navHistory, setNavHistory] = useState([]);

  const [workspaces, setWorkspaces] = useState([]);
  const [currentWorkspaceId, setCurrentWorkspaceId] = useState('');
  const [currentWorkspaceName, setCurrentWorkspaceName] = useState('');

  const [boards, setBoards] = useState({});
  const [currentBoardId, setCurrentBoardId] = useState('');

  // Board writes are full-snapshot PUTs guarded by a server-authoritative `version`: a write
  // whose declared version is stale is rejected 409. The optimistic UI cannot bump the version
  // (it's server-owned), so two writes that overlap — a second board action fired before the first
  // write's response lands — would both declare the same version and the later ones would 409 and be
  // discarded, silently dropping a just-created card. We fix that by (1) serialising writes per board
  // so they never overlap, and (2) stamping each write with the freshest confirmed version at send
  // time, not the stale one carried in React state. `boardVersions` is the last version the server
  // confirmed for each board; `boardWriteChain` is the per-board promise tail we append to.
  const boardVersions = useRef<Record<string, number>>({});
  const boardWriteChain = useRef<Record<string, Promise<unknown>>>({});
  const rememberVersion = (board: any) => {
    if (board && board.id != null && board.version != null) boardVersions.current[board.id] = board.version;
  };

  const [isHydrated, setIsHydrated] = useState(false);
  const [isDemoLoading, setIsDemoLoading] = useState(false);
  // App-level error surface so failed calls don't render as silent empty screens.
  const [appError, setAppError] = useState<AppError | null>(null);
  // Feedback is reachable from every screen, so users meet it wherever they hit a rough edge rather
  // than having to go looking for it.
  const [showFeedback, setShowFeedback] = useState(false);
  const [showAbout, setShowAbout] = useState(false);

  // Load the workspace list (mount + Retry). Surfaces a banner on failure instead of an empty home.
  const hydrateWorkspaces = async () => {
    try {
      const list = await apiListWorkspaces();
      setWorkspaces(list);
      setAppError(null);
    } catch (_) {
      setAppError({
        message: 'Could not load your workspaces. Check your connection and try again.',
        onRetry: () => { setAppError(null); hydrateWorkspaces(); },
      });
    } finally {
      setIsHydrated(true);
    }
  };

  // Load workspace list on mount
  useEffect(() => {
    hydrateWorkspaces();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reset scroll on navigation. Nothing here scrolls in a container -- every screen is
  // `min-h-screen` and the WINDOW scrolls -- so moving between views keeps whatever offset the
  // previous screen had. Scroll the workspace list, open a board, and you land partway down it
  // with the board header (and the Import notes button) above the fold. A new screen should start
  // at the top, the way a page navigation would.
  // `instant` on purpose: a smooth scroll here reads as the page drifting, not as navigating.
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' as ScrollBehavior });
  }, [currentView, currentBoardId, currentWorkspaceId]);

  const loadWorkspaceBoards = async (workspaceId) => {
    try {
      const list = await apiListWorkspaceBoards(workspaceId);
      list.forEach(rememberVersion);
      setBoards(toBoardMap(list));
      setCurrentBoardId(list[0]?.id ?? '');
    } catch (_) {
      setBoards({});
      setCurrentBoardId('');
    }
  };

  // Navigation helpers
  const openWorkspace = (wsId, wsName) => {
    setNavHistory(prev => [...prev, { view: currentView, workspaceId: currentWorkspaceId, workspaceName: currentWorkspaceName, boardId: currentBoardId }]);
    setCurrentWorkspaceId(wsId);
    setCurrentWorkspaceName(wsName);
    setCurrentView('workspace');
    loadWorkspaceBoards(wsId);
  };

  const openBoard = (boardId) => {
    setNavHistory(prev => [...prev, { view: currentView, workspaceId: currentWorkspaceId, workspaceName: currentWorkspaceName, boardId: currentBoardId }]);
    setCurrentBoardId(boardId);
    setCurrentView('board');
  };

  const goBack = () => {
    if (navHistory.length > 0) {
      const prev = navHistory[navHistory.length - 1];
      setNavHistory(h => h.slice(0, -1));
      setCurrentView(prev.view);
      setCurrentWorkspaceId(prev.workspaceId ?? '');
      setCurrentWorkspaceName(prev.workspaceName ?? '');
      setCurrentBoardId(prev.boardId ?? '');
      if (prev.view === 'workspace' && prev.workspaceId) {
        loadWorkspaceBoards(prev.workspaceId);
      }
    } else {
      setCurrentView('welcome');
    }
  };

  const goHome = () => {
    setNavHistory([]);
    setCurrentView('welcome');
  };

  const createNewBoard = async (payload) => {
    try {
      const createdBoard = await apiCreateBoard({ ...payload, workspaceId: currentWorkspaceId || null });
      rememberVersion(createdBoard);
      setBoards(prev => ({ ...prev, [createdBoard.id]: createdBoard }));
      openBoard(createdBoard.id);
    } catch (_) {
      await loadWorkspaceBoards(currentWorkspaceId);
    }
  };

  const deleteBoard = async (boardId) => {
    const newBoards = { ...boards };
    delete newBoards[boardId];
    setBoards(newBoards);
    if (currentBoardId === boardId) setCurrentBoardId(Object.keys(newBoards)[0] ?? '');
    try {
      await apiDeleteBoard(boardId);
    } catch (_error) {
      try {
        await loadWorkspaceBoards(currentWorkspaceId);
      } catch (__error) { /* keep optimistic state */ }
    }
  };

  const updateBoard = (boardId, updatedBoard) => {
    // Reflect the change immediately. The optimistic board keeps whatever `version` it had — only the
    // server can advance it — so the send step below overrides it with the freshest confirmed value.
    const optimistic = { ...updatedBoard, updatedAt: new Date().toISOString() };
    setBoards(prev => ({ ...prev, [boardId]: optimistic }));

    const send = async () => {
      // Serialised, so by now any prior write for this board has confirmed and updated boardVersions.
      const version = boardVersions.current[boardId] ?? optimistic.version;
      try {
        const saved = await apiSyncBoard(boardId, { ...optimistic, version });
        rememberVersion(saved);
        setBoards(prev => ({ ...prev, [boardId]: saved }));
      } catch (err: any) {
        // A 409 here means another CLIENT moved the board (our own writes are serialised away). The
        // optimistic snapshot is the full desired state, so refetch the current version, reapply on
        // top of it, and retry once before giving up and reloading from the server.
        if (err?.status === 409) {
          try {
            const fresh = await apiGetBoard(boardId);
            const retried = await apiSyncBoard(boardId, { ...optimistic, version: fresh.version });
            rememberVersion(retried);
            setBoards(prev => ({ ...prev, [boardId]: retried }));
            return;
          } catch (_) { /* fall through to a clean reload */ }
        }
        // Anything else is NOT a conflict, and reloading silently is how a rejected write reads as
        // "my change vanished a moment after I made it".
        // A 4xx here means the server refused this snapshot outright (a bad field, a
        // limit exceeded); the change is not saved and no retry will help, so reload to match the
        // server but SAY SO, rather than letting the work disappear without a word.
        if (err?.status && err.status !== 409) {
          setAppError({
            message:
              'That change was not saved — the server rejected it, so the board has been reloaded. ' +
              (typeof err.detail === 'string' ? err.detail : `(${err.status})`)
          });
        }
        await loadWorkspaceBoards(currentWorkspaceId);
      }
    };

    const tail = (boardWriteChain.current[boardId] ?? Promise.resolve()).catch(() => {}).then(send);
    boardWriteChain.current[boardId] = tail;
    return tail;
  };

  // Refetch a single board from the server. The note-import flow calls this after it creates cards:
  // the board picks up the new cards *and* the server-bumped `version`, so the next ordinary snapshot
  // write declares the current version and is not rejected as stale.
  const reloadBoard = async (boardId) => {
    try {
      const fresh = await apiGetBoard(boardId);
      rememberVersion(fresh);
      setBoards(prev => ({ ...prev, [boardId]: fresh }));
    } catch (_) {
      /* leave the current board in place; the next sync will reconcile */
    }
  };

  const loadDemoData = async () => {
    setIsDemoLoading(true);
    try {
      // Delete any existing "Demo Workspace" so we start fresh
      const existing = workspaces.find(w => w.name === 'Demo Workspace');
      if (existing) await apiDeleteWorkspace(existing.id);

      // Create a fresh demo workspace
      const demoWs = await apiCreateWorkspace('Demo Workspace');

      for (const config of DEMO_BOARDS) {
        const { cards: cardDefs, ...createPayload } = config;
        const createdBoard = await apiCreateBoard({ ...createPayload, workspaceId: demoWs.id });

        const now = new Date().toISOString();
        const columnIds = createdBoard.columnOrder;
        const memberIds = createdBoard.teamMembers.map((m) => m.id);
        const columns = {};
        columnIds.forEach((colId) => { columns[colId] = { ...createdBoard.columns[colId], cardIds: [] }; });
        // Card ids are deterministic within a board (`demo_<board>_<index>`), so a demo card can
        // name a blocker/dependency by its INDEX in the same board and we resolve it to the id
        // here. That is the only way demo data can express dependencies — the ids do not exist
        // until the board is created. Cross-board links are not
        // supported and not needed: a blocker the assistant cannot see in scope is not useful.
        const cardIdFor = (i) => `demo_${createdBoard.id}_${i}`;
        const cards = {};
        cardDefs.forEach((cardDef, idx) => {
          const cardId = cardIdFor(idx);
          const colId = columnIds[Math.min(cardDef.columnIndex, columnIds.length - 1)];
          cards[cardId] = {
            id: cardId, title: cardDef.title, description: cardDef.description || '',
            // Demo due dates are day OFFSETS resolved against today, so the board shows the same
            // realistic spread (a few overdue, several this week, most within the month) on every
            // load instead of decaying into "everything is late". See src/data/demoDates.ts.
            priority: cardDef.priority || 'medium', dueDate: resolveCardDueDate(cardDef),
            assignees: (cardDef.assigneeIndices || []).map((i) => memberIds[i]).filter(Boolean),
            createdAt: now, completed: cardDef.completed || false,
            jiraKey: cardDef.jiraKey || null,
            storyPoints: cardDef.storyPoints || null,
            labels: cardDef.labels || [],
            blockedBy: (cardDef.blockedByIndices || []).map(cardIdFor),
            dependsOn: (cardDef.dependsOnIndices || []).map(cardIdFor),
          };
          columns[colId].cardIds.push(cardId);
        });
        await apiSyncBoard(createdBoard.id, { ...createdBoard, cards, columns });
      }

      // Refresh workspace list and navigate into the demo workspace
      const wsList = await apiListWorkspaces();
      setWorkspaces(wsList);
      await loadWorkspaceBoards(demoWs.id);
      setCurrentWorkspaceId(demoWs.id);
      setCurrentWorkspaceName(demoWs.name);
      setNavHistory([]);
      setCurrentView('workspace');
    } catch (_err) {
      const wsList = await apiListWorkspaces().catch(() => workspaces);
      setWorkspaces(wsList);
      setAppError({ message: 'Could not load the demo data. Please try again.' });
    } finally {
      setIsDemoLoading(false);
    }
  };

  const toggleCardCompletionRemote = (boardId, cardId, completed, optimisticBoard) => {
    setBoards(prev => ({ ...prev, [boardId]: { ...optimisticBoard, updatedAt: new Date().toISOString() } }));
    // Toggle rides the same per-board write chain as snapshot saves: it mutates the board and bumps
    // the server version too, so letting it overlap a snapshot write would reopen the stale-version
    // window this fix closes.
    const send = async () => {
      try {
        const saved = await apiToggleCardComplete(boardId, cardId, completed);
        rememberVersion(saved);
        setBoards(prev => ({ ...prev, [boardId]: saved }));
      } catch (_) {
        await loadWorkspaceBoards(currentWorkspaceId);
      }
    };
    const tail = (boardWriteChain.current[boardId] ?? Promise.resolve()).catch(() => {}).then(send);
    boardWriteChain.current[boardId] = tail;
    return tail;
  };

  if (!isHydrated) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center">
        <p className="text-fg-muted text-sm">Loading…</p>
      </div>
    );
  }

  // ── Welcome (workspace list) ─────────────────────────────────────────────
  if (currentView === 'welcome') {
    return (
      <>
      <ErrorBanner error={appError} onDismiss={() => setAppError(null)} />
      {showFeedback && <FeedbackModal onClose={() => setShowFeedback(false)} page={currentView} />}
      {showAbout && <Suspense fallback={null}><AboutModal onClose={() => setShowAbout(false)} /></Suspense>}
      <div style={{ position: 'fixed', top: '0.75rem', right: '1rem', zIndex: 9999, display: 'flex', alignItems: 'center' }}>
        <button
          onClick={() => setShowAbout(true)}
          title="About Columnist — product roadmap, architecture, tech stack"
          style={{ padding: '0.375rem 0.75rem', backgroundColor: 'rgb(var(--surface))', color: 'rgb(var(--text))', fontSize: '0.8125rem', fontWeight: 600, borderRadius: '0.5rem', border: '1px solid rgb(var(--border-strong))', cursor: 'pointer', whiteSpace: 'nowrap', marginRight: '0.5rem' }}
        >
          ℹ️ About
        </button>
        <button
          onClick={() => setShowFeedback(true)}
          title="Send feedback"
          style={{ padding: '0.375rem 0.75rem', backgroundColor: 'rgb(var(--surface))', color: 'rgb(var(--text))', fontSize: '0.8125rem', fontWeight: 600, borderRadius: '0.5rem', border: '1px solid rgb(var(--border-strong))', cursor: 'pointer', whiteSpace: 'nowrap', marginRight: '0.5rem' }}
        >
          💬 Feedback
        </button>
        <ThemeToggle />
        <AccountMenu />
      </div>
      <WorkspaceListScreen
        workspaces={workspaces}
        onOpenWorkspace={openWorkspace}
        onCreateWorkspace={async (name) => {
          try {
            const ws = await apiCreateWorkspace(name);
            setWorkspaces(prev => [ws, ...prev]);
            openWorkspace(ws.id, ws.name);
          } catch (_) {
            setAppError({ message: `Could not create workspace "${name}". Please try again.` });
          }
        }}
        onLoadDemo={loadDemoData}
        onRenameWorkspace={async (id, name) => {
          try {
            const updated = await apiRenameWorkspace(id, name);
            setWorkspaces(prev => prev.map(w => w.id === id ? updated : w));
          } catch (_) {
            setAppError({ message: 'Could not rename the workspace. Please try again.' });
          }
        }}
        onDeleteWorkspace={async (id) => {
          try {
            await apiDeleteWorkspace(id);
            setWorkspaces(prev => prev.filter(w => w.id !== id));
          } catch (_) {
            setAppError({ message: 'Could not delete the workspace. Please try again.' });
          }
        }}
        isDemoLoading={isDemoLoading}
      />
      </>
    );
  }

  // ── Global nav (workspace + board views) ────────────────────────────────
  const isInBoard = currentView === 'board';
  const currentBoard = isInBoard ? (boards[currentBoardId] ?? null) : null;
  const NAV_H = 52;

  const globalNav = (
    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 9999, backgroundColor: 'rgb(var(--nav-bg))', borderBottom: '1px solid rgb(var(--nav-border))', boxShadow: '0 1px 4px rgba(0,0,0,0.08)', height: `${NAV_H}px`, display: 'flex', alignItems: 'center' }}>
      <div style={{ maxWidth: '80rem', margin: '0 auto', padding: '0 1.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem', width: '100%' }}>
        <button onClick={goHome} style={{ padding: '0.375rem 0.75rem', backgroundColor: 'rgb(var(--accent))', color: 'rgb(var(--text-inverted))', fontSize: '0.8125rem', fontWeight: 700, borderRadius: '0.5rem', border: 'none', cursor: 'pointer', whiteSpace: 'nowrap' }}>
          ⌂ Home
        </button>
        <button onClick={goBack} style={{ padding: '0.375rem 0.75rem', backgroundColor: 'rgb(var(--surface-sunken))', color: 'rgb(var(--text))', fontSize: '0.8125rem', fontWeight: 600, borderRadius: '0.5rem', border: '1px solid rgb(var(--border-strong))', cursor: 'pointer', whiteSpace: 'nowrap' }}>
          ← Back
        </button>
        <span style={{ color: 'rgb(var(--border-strong))', margin: '0 0.25rem' }}>|</span>
        <span onClick={goHome} style={{ fontWeight: 700, color: 'rgb(var(--accent))', fontSize: '0.875rem', cursor: 'pointer', whiteSpace: 'nowrap' }}>Columnist</span>
        <span style={{ color: 'rgb(var(--text-subtle))' }}>›</span>
        <span onClick={() => { setCurrentView('workspace'); setCurrentBoardId(''); }} style={{ color: isInBoard ? 'rgb(var(--text-muted))' : 'rgb(var(--accent))', fontSize: '0.875rem', cursor: 'pointer', fontWeight: isInBoard ? 400 : 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '12rem' }}>
          {currentWorkspaceName}
        </span>
        {isInBoard && currentBoard && (
          <>
            <span style={{ color: 'rgb(var(--text-subtle))' }}>›</span>
            <span style={{ color: 'rgb(var(--text))', fontSize: '0.875rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {currentBoard.projectInfo.title}
            </span>
          </>
        )}
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setShowAbout(true)}
          title="About Columnist — product roadmap, architecture, tech stack"
          style={{ padding: '0.375rem 0.75rem', backgroundColor: 'rgb(var(--surface))', color: 'rgb(var(--text))', fontSize: '0.8125rem', fontWeight: 600, borderRadius: '0.5rem', border: '1px solid rgb(var(--border-strong))', cursor: 'pointer', whiteSpace: 'nowrap', marginRight: '0.5rem' }}
        >
          ℹ️ About
        </button>
        <button
          onClick={() => setShowFeedback(true)}
          title="Send feedback"
          style={{ padding: '0.375rem 0.75rem', backgroundColor: 'rgb(var(--surface))', color: 'rgb(var(--text))', fontSize: '0.8125rem', fontWeight: 600, borderRadius: '0.5rem', border: '1px solid rgb(var(--border-strong))', cursor: 'pointer', whiteSpace: 'nowrap', marginRight: '0.5rem' }}
        >
          💬 Feedback
        </button>
        <ThemeToggle />
        <AccountMenu />
      </div>
    </div>
  );

  // ── Workspace view (boards list) ─────────────────────────────────────────
  if (currentView === 'workspace') {
    // This label is shown to the user by the chat panel, so it has to read as a sentence: no
    // "all 1 boards", and no board count at all before the list has loaded.
    const boardCount = Object.keys(boards).length;
    const workspaceScopeLabel = boardCount === 0
      ? currentWorkspaceName
      : `all ${boardCount} board${boardCount === 1 ? '' : 's'} in ${currentWorkspaceName}`;
    return (
      <>
        <ErrorBanner error={appError} onDismiss={() => setAppError(null)} />
      {showFeedback && <FeedbackModal onClose={() => setShowFeedback(false)} page={currentView} />}
      {showAbout && <Suspense fallback={null}><AboutModal onClose={() => setShowAbout(false)} /></Suspense>}
        {globalNav}
        <div style={{ paddingTop: `${NAV_H}px` }}>
          {/* Workspace level: no active board, so the assistant sees every board in THIS workspace
              — the server fences it to the caller's accessible boards intersected with the
              workspace, never wider. That cross-board reach is the point of having chat here at
              all — a single board is small enough to just look at. The label says so out loud. */}
          <WorkspaceWithChat
            onOpenFeedback={() => setShowFeedback(true)}
            activeBoardId={null}
            activeWorkspaceId={currentWorkspaceId}
            scopeLabel={workspaceScopeLabel}
            scopeName={currentWorkspaceName}
          >
            <WorkspaceBoardsView
              workspaceName={currentWorkspaceName}
              boards={boards}
              onSelectBoard={openBoard}
              onCreateBoard={createNewBoard}
              onDeleteBoard={deleteBoard}
            />
          </WorkspaceWithChat>
        </div>
      </>
    );
  }

  // ── Board view ────────────────────────────────────────────────────────────
  if (!currentBoard) {
    setCurrentView('workspace');
    return null;
  }

  return (
    <>
      <ErrorBanner error={appError} onDismiss={() => setAppError(null)} />
      {showFeedback && <FeedbackModal onClose={() => setShowFeedback(false)} page={currentView} />}
      {showAbout && <Suspense fallback={null}><AboutModal onClose={() => setShowAbout(false)} /></Suspense>}
      {globalNav}
      <div style={{ paddingTop: `${NAV_H}px` }}>
        <WorkspaceWithChat
          onOpenFeedback={() => setShowFeedback(true)}
          activeBoardId={currentBoard.id}
          activeWorkspaceId={currentWorkspaceId}
          scopeLabel={currentBoard.projectInfo?.title || 'this board'}
          scopeName={currentBoard.projectInfo?.title || 'this board'}
        >
          <KanbanBoard
            board={currentBoard}
            onUpdateBoard={(updated) => updateBoard(currentBoard.id, updated)}
            onToggleCardCompletion={(cardId, completed, optimistic) =>
              toggleCardCompletionRemote(currentBoard.id, cardId, completed, optimistic)
            }
            onReloadBoard={() => reloadBoard(currentBoard.id)}
            workspaceName={currentWorkspaceName}
          />
        </WorkspaceWithChat>
      </div>
    </>
  );
}
