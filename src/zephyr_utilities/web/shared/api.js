/** Authenticated JSON API shared by the shell and every mounted feature. */
export function createApi(session, fetcher = fetch) {
  return async function api(path, options = {}) {
    if (!/^\/api\//.test(path) || path.startsWith("//")) throw new Error("Only local API paths are supported.");
    const headers = {"X-Session-Token": session.token, ...options.headers};
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetcher(path, {
      ...options,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  };
}
