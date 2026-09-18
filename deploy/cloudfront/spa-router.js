// CloudFront Function (viewer request) — SPA deep-link routing WITHOUT custom error responses.
//
// WHY THIS EXISTS
// The distribution serves a single-page app, so a deep link like /board/abc must return
// index.html rather than 404. The usual way to do that is a CustomErrorResponse mapping
// 403/404 -> 200 /index.html. But **custom error responses are distribution-wide**: they apply to
// EVERY behavior, including /api/*. That silently corrupts the API:
//
//   * a real 403 from /api/v1/auth/me   -> arrives at the browser as 200 text/html
//   * a real 404 for a deleted board    -> arrives as 200 text/html
//
// So a failed API call looks like a success, the SPA parses HTML as JSON, and the error the user
// sees names neither the endpoint nor the cause — an unauthorized /auth/me, for example, looks
// like a rejected password.
//
// This function does the SPA rewrite at the edge instead, attached ONLY to the default (S3)
// behavior — so /api/* keeps its real status codes and the error responses can be deleted.
//
// DEPLOY
//   1. CloudFront -> Functions -> Create function, name `spa-router`, paste this, Publish.
//   2. Distribution -> Behaviors -> Default (*) -> Edit -> Function associations ->
//      Viewer request = spa-router. Save. (Do NOT attach it to /api/* .)
//   3. Distribution -> Error pages -> DELETE both custom responses (403 -> 200 /index.html and
//      404 -> 200 /index.html). This is the step that fixes the API; the function only preserves
//      deep links once they are gone.
//   4. Verify:
//        curl -s -o /dev/null -w '%{http_code}\n' https://<domain>/board/does-not-exist   # 200
//        curl -s -i https://<domain>/api/v1/auth/me | head -3                             # real 401/403
//
// Order matters: do 1 and 2 BEFORE 3, or deep links break in the gap.

function handler(event) {
  var request = event.request;
  var uri = request.uri;

  // Anything under /api/ is the backend's business — never rewrite it. This behavior should not
  // receive /api/* at all (a separate behavior handles it), but a path-pattern change should not
  // be able to turn API calls into HTML.
  if (uri.startsWith('/api/')) {
    return request;
  }

  // A path with a file extension is a real asset (/assets/index-x.js, /favicon.ico). Let it
  // through so a genuinely missing file still 404s instead of masquerading as the app.
  var lastSegment = uri.substring(uri.lastIndexOf('/') + 1);
  if (lastSegment.indexOf('.') !== -1) {
    return request;
  }

  // Everything else is a client-side route: serve the SPA shell and let the router handle it.
  request.uri = '/index.html';
  return request;
}
