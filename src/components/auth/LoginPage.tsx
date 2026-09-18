// Columnist — custom branded login (BFF / Cognito). No tokens touch JS: every call goes to
// /auth/* and the backend sets an httpOnly session cookie. Screens: sign-in, first-login
// set-password (Cognito NEW_PASSWORD_REQUIRED), forgot-password, confirm-reset.
import React, { useState } from 'react';
import { Grid3X3, Loader2, Eye, EyeOff, ArrowLeft, CheckCircle2 } from 'lucide-react';
import { useAuth } from '../../auth/AuthContext';

type Mode = 'signin' | 'newPassword' | 'forgot' | 'confirmForgot';

const MIN_PASSWORD = 12;

export const LoginPage = ({ inviteToken }: { inviteToken?: string }) => {
  const { login, setPassword, forgotPassword, confirmForgotPassword, authError } = useAuth();

  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword_] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [code, setCode] = useState('');
  const [session, setSession] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(inviteToken ? "You've been invited — sign in to get started." : null);

  const isInvite = Boolean(inviteToken);

  const resetFeedback = () => {
    setError(null);
    setNotice(null);
  };

  const submitSignin = async (e: React.FormEvent) => {
    e.preventDefault();
    resetFeedback();
    setBusy(true);
    try {
      const result = await login(email.trim(), password);
      if (result.status === 'NEW_PASSWORD_REQUIRED') {
        setSession(result.session);
        setEmail(result.email);
        setPassword_('');
        setMode('newPassword');
        setNotice('First sign-in — choose a permanent password to finish setting up your account.');
      }
      // status OK → AuthProvider flips to authenticated and this page unmounts.
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  const submitNewPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    resetFeedback();
    if (newPassword.length < MIN_PASSWORD) return setError(`Password must be at least ${MIN_PASSWORD} characters.`);
    if (newPassword !== confirm) return setError('Passwords do not match.');
    setBusy(true);
    try {
      await setPassword(email.trim(), newPassword, session);
      // success → authenticated, page unmounts.
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not set password');
    } finally {
      setBusy(false);
    }
  };

  const submitForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    resetFeedback();
    setBusy(true);
    try {
      await forgotPassword(email.trim());
      setMode('confirmForgot');
      // Deliberately generic — the backend never reveals whether the email exists.
      setNotice('If that email is registered, a reset code is on its way. Enter it below.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start reset');
    } finally {
      setBusy(false);
    }
  };

  const submitConfirmForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    resetFeedback();
    if (newPassword.length < MIN_PASSWORD) return setError(`Password must be at least ${MIN_PASSWORD} characters.`);
    if (newPassword !== confirm) return setError('Passwords do not match.');
    setBusy(true);
    try {
      await confirmForgotPassword(email.trim(), code.trim(), newPassword);
      setMode('signin');
      setPassword_('');
      setNewPassword('');
      setConfirm('');
      setCode('');
      setNotice('Password reset. Sign in with your new password.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not reset password');
    } finally {
      setBusy(false);
    }
  };

  const goSignin = () => {
    resetFeedback();
    setMode('signin');
  };

  const title =
    mode === 'signin' ? (isInvite ? 'Welcome' : 'Sign in') :
    mode === 'newPassword' ? 'Set your password' :
    mode === 'forgot' ? 'Reset your password' :
    'Enter your reset code';

  const subtitle =
    mode === 'signin' ? 'Plan the work. Columnist keeps the board.' :
    mode === 'newPassword' ? 'One last step to secure your account.' :
    mode === 'forgot' ? "We'll email you a code to set a new password." :
    'Check your inbox for the six-digit code.';

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-gradient-to-b from-slate-950 via-slate-900 to-teal-950">
      {/* subtle horizon glow */}
      <div className="pointer-events-none absolute inset-x-0 top-1/4 h-64 bg-teal-500/10 blur-3xl" aria-hidden />

      <div className="relative w-full max-w-sm">
        {/* Brand */}
        <div className="flex flex-col items-center text-center mb-7">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-amber-400 to-amber-600 flex items-center justify-center shadow-lg shadow-amber-900/40 ring-1 ring-amber-300/40">
            <Grid3X3 size={26} className="text-slate-900" strokeWidth={2.4} />
          </div>
          <h1 className="mt-4 text-2xl font-bold tracking-tight text-fg-inverted">Columnist</h1>
          <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
        </div>

        {/* Card */}
        <div className="rounded-2xl border border-white/10 bg-slate-900/70 backdrop-blur-sm shadow-2xl shadow-black/40 p-6">
          <h2 className="text-base font-semibold text-fg-inverted mb-4">{title}</h2>

          {notice && (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-teal-400/20 bg-teal-400/10 px-3 py-2 text-[13px] text-teal-200">
              <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
              <span>{notice}</span>
            </div>
          )}
          {(error || authError) && (
            <div className="mb-4 rounded-lg border border-rose-400/20 bg-rose-500/10 px-3 py-2 text-[13px] text-rose-200">
              {error || authError}
            </div>
          )}

          {mode === 'signin' && (
            <form onSubmit={submitSignin} className="space-y-3">
              <Field label="Email" type="email" value={email} onChange={setEmail} autoFocus placeholder="you@example.com" required />
              <PasswordField
                label="Password"
                value={password}
                onChange={setPassword_}
                show={showPw}
                toggle={() => setShowPw((s) => !s)}
                required
              />
              <div className="flex justify-end">
                <button type="button" onClick={() => { resetFeedback(); setMode('forgot'); }} className="text-xs text-slate-400 hover:text-slate-200">
                  Forgot password?
                </button>
              </div>
              <SubmitButton busy={busy} label="Sign in" />
            </form>
          )}

          {mode === 'newPassword' && (
            <form onSubmit={submitNewPassword} className="space-y-3">
              <Field label="Email" type="email" value={email} onChange={setEmail} disabled />
              <PasswordField label="New password" value={newPassword} onChange={setNewPassword} show={showPw} toggle={() => setShowPw((s) => !s)} autoFocus required />
              <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} show={showPw} toggle={() => setShowPw((s) => !s)} required />
              <p className="text-[11px] text-slate-500">At least {MIN_PASSWORD} characters, with upper and lowercase letters and a number.</p>
              <SubmitButton busy={busy} label="Set password & continue" />
            </form>
          )}

          {mode === 'forgot' && (
            <form onSubmit={submitForgot} className="space-y-3">
              <Field label="Email" type="email" value={email} onChange={setEmail} autoFocus placeholder="you@example.com" required />
              <SubmitButton busy={busy} label="Send reset code" />
              <BackLink onClick={goSignin} />
            </form>
          )}

          {mode === 'confirmForgot' && (
            <form onSubmit={submitConfirmForgot} className="space-y-3">
              <Field label="Reset code" type="text" value={code} onChange={setCode} autoFocus placeholder="123456" required />
              <PasswordField label="New password" value={newPassword} onChange={setNewPassword} show={showPw} toggle={() => setShowPw((s) => !s)} required />
              <PasswordField label="Confirm new password" value={confirm} onChange={setConfirm} show={showPw} toggle={() => setShowPw((s) => !s)} required />
              <SubmitButton busy={busy} label="Reset password" />
              <BackLink onClick={goSignin} />
            </form>
          )}
        </div>

        <p className="mt-6 text-center text-[11px] text-slate-500">Invite-only · secured by Amazon Cognito</p>
      </div>
    </div>
  );
};

// ── small presentational helpers ──
const inputClass =
  'w-full rounded-lg border border-white/10 bg-slate-800/60 px-3 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-amber-400/60 focus:ring-1 focus:ring-amber-400/40 disabled:opacity-60';

function Field({
  label, type, value, onChange, placeholder, autoFocus, required, disabled
}: {
  label: string; type: string; value: string; onChange: (v: string) => void;
  placeholder?: string; autoFocus?: boolean; required?: boolean; disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-300">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoFocus={autoFocus}
        required={required}
        disabled={disabled}
        className={inputClass}
      />
    </label>
  );
}

function PasswordField({
  label, value, onChange, show, toggle, autoFocus, required
}: {
  label: string; value: string; onChange: (v: string) => void; show: boolean; toggle: () => void;
  autoFocus?: boolean; required?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-300">{label}</span>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoFocus={autoFocus}
          required={required}
          className={inputClass + ' pr-10'}
        />
        <button
          type="button"
          onClick={toggle}
          aria-label={show ? 'Hide password' : 'Show password'}
          className="absolute inset-y-0 right-2 flex items-center text-slate-400 hover:text-slate-200"
        >
          {show ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </label>
  );
}

function SubmitButton({ busy, label }: { busy: boolean; label: string }) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="mt-1 w-full flex items-center justify-center gap-2 rounded-lg bg-gradient-to-br from-amber-400 to-amber-600 py-2.5 text-sm font-semibold text-slate-900 hover:brightness-105 disabled:opacity-70 transition"
    >
      {busy && <Loader2 size={16} className="animate-spin" />}
      {label}
    </button>
  );
}

function BackLink({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="mt-1 flex w-full items-center justify-center gap-1.5 text-xs text-slate-400 hover:text-slate-200">
      <ArrowLeft size={14} /> Back to sign in
    </button>
  );
}
