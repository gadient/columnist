// App root. Wraps everything in AuthProvider and gates on auth state:
//  - backend unreachable on boot (deployed build): show OfflineScreen — checked first.
//  - auth OFF (local dev): render the app openly.
//  - auth ON + not signed in: show the branded LoginPage (whole app is behind it).
//  - auth ON + signed in: render the app.
// The ?invite= param just tailors the login copy — the sign-in itself is email/password (BFF).
import React from 'react';
import { Loader2 } from 'lucide-react';
import App from './App';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { LoginPage } from './components/auth/LoginPage';
import { OfflineScreen } from './components/common/OfflineScreen';
import { ThemeProvider } from './theme/ThemeContext';

function Gate() {
  const { authEnabled, status } = useAuth();
  const inviteToken = new URLSearchParams(window.location.search).get('invite') ?? undefined;

  // Backend unreachable on boot (deployed build, service down/paused). Fail closed to a maintenance
  // screen — checked before the auth gates because `authEnabled` is unknown when config never
  // answered, so the open-app fall-through must not win. See AuthContext bootstrap.
  if (status === 'offline') {
    return <OfflineScreen />;
  }

  // While we're still learning whether auth is on / hydrating the session, hold a brief splash.
  if (authEnabled && status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-400">
        <Loader2 className="animate-spin" />
      </div>
    );
  }

  if (authEnabled && status !== 'authenticated') {
    return <LoginPage inviteToken={inviteToken} />;
  }

  return <App />;
}

export default function Root() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </ThemeProvider>
  );
}
