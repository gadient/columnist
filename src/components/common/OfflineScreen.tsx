// Shown when the SPA boots but the backend never answers `/auth/config` (a deployed build only —
// see AuthContext bootstrap). A CDN-served build loads even while the backend is paused or down,
// so without this a visitor would fall through to the open, no-auth app shell. Fail closed to an
// honest "temporarily offline" screen instead. Branding mirrors LoginPage so the two read as one app.
import { Grid3X3, RefreshCw } from 'lucide-react';

export function OfflineScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-gradient-to-b from-slate-950 via-slate-900 to-teal-950">
      {/* subtle glow */}
      <div className="pointer-events-none absolute inset-x-0 top-1/4 h-64 bg-teal-500/10 blur-3xl" aria-hidden />

      <div className="relative w-full max-w-sm text-center">
        <div className="flex flex-col items-center mb-7">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-amber-400 to-amber-600 flex items-center justify-center shadow-lg shadow-amber-900/40 ring-1 ring-amber-300/40">
            <Grid3X3 size={26} className="text-slate-900" strokeWidth={2.4} />
          </div>
          <h1 className="mt-4 text-2xl font-bold tracking-tight text-fg-inverted">Columnist</h1>
        </div>

        <div className="rounded-2xl border border-white/10 bg-slate-900/70 backdrop-blur-sm shadow-2xl shadow-black/40 p-6">
          <h2 className="text-base font-semibold text-fg-inverted mb-2">Columnist is offline</h2>
          <p className="text-sm text-slate-400">
            We can&rsquo;t reach the service right now. Please check back shortly.
          </p>

          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-5 inline-flex items-center gap-2 rounded-lg bg-amber-500 hover:bg-amber-400 px-4 py-2 text-sm font-semibold text-slate-900 transition-colors"
          >
            <RefreshCw size={16} strokeWidth={2.4} />
            Try again
          </button>
        </div>
      </div>
    </div>
  );
}
