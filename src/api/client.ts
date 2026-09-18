export const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';

// BFF auth: the session rides in an httpOnly cookie the browser sends automatically, so every
// call opts into credentials. For the CSRF double-submit defence we echo the readable CSRF
// cookie in a header on unsafe methods — a cross-site page can read neither cookie, so it can't
// forge a matching header. The cookie name is fixed by the backend (`csrf_cookie_name`).
const CSRF_COOKIE = 'columnist_csrf';
const CSRF_HEADER = 'X-CSRF-Token';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}

export const apiRequest = async (path, options: RequestInit = {}) => {
  const method = (options.method || 'GET').toUpperCase();
  const csrfHeader: Record<string, string> = {};
  if (!SAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE);
    if (token) csrfHeader[CSRF_HEADER] = token;
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...csrfHeader,
      ...((options.headers as Record<string, string>) || {})
    }
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let detail: any = undefined;
    try {
      const payload = await response.json();
      detail = payload?.detail ?? payload?.error;
      // `detail` can be a structured object (e.g. the version-conflict envelope
      // {code, message, action}); prefer its message for display but keep the object on the error
      // so callers can branch on `detail.code` / `err.status`.
      message = (typeof detail === 'string' ? detail : detail?.message) || message;
    } catch (_error) {
      // Ignore JSON parse failures for non-JSON error bodies.
    }
    const err: any = new Error(message);
    err.status = response.status;
    err.detail = detail;
    throw err;
  }

  if (response.status === 204) {
    return null;
  }

  // A 2xx does NOT guarantee JSON. A CDN's SPA fallback can rewrite origin 403/404 into
  // `200 text/html` serving index.html, so a failed API call arrives here looking like a success
  // and `response.json()` throws a parse error naming neither the endpoint nor the cause. In
  // Safari that reads "The string did not match the expected pattern", which makes an
  // unauthorized /auth/me look like a rejected password.
  // Check the content type and say what actually came back.
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('json')) {
    const body = (await response.text()).trim();
    const looksLikeHtml = body.startsWith('<');
    throw new Error(
      looksLikeHtml
        ? `${path} returned an HTML page instead of JSON (status ${response.status}). ` +
          `The request probably never reached the API — a CDN or proxy returned an error page ` +
          `instead of the API response.`
        : `${path} returned ${contentType || 'no content-type'} instead of JSON ` +
          `(status ${response.status}): ${body.slice(0, 120)}`
    );
  }

  return response.json();
};
