import test from "node:test";
import assert from "node:assert/strict";
import {createApi} from "../../src/zephyr_utilities/web/shared/api.js";

test("shared API carries session authentication and serializes request data once", async () => {
  const controller = new AbortController();
  const api = createApi({token: "local-token"}, async (path, options) => {
    assert.equal(path, "/api/workspace");
    assert.equal(options.method, "PATCH");
    assert.equal(options.headers["X-Session-Token"], "local-token");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.equal(options.body, JSON.stringify({path: "/work/my project"}));
    assert.equal(options.signal, controller.signal);
    return {ok: true, json: async () => ({path: "/work/my project"})};
  });
  assert.deepEqual(await api("/api/workspace", {method: "PATCH", body: {path: "/work/my project"}, signal: controller.signal}), {path: "/work/my project"});
});

test("GET requests have no JSON request body and report API errors clearly", async () => {
  const api = createApi({token: "local-token"}, async (_, options) => {
    assert.equal(options.body, undefined);
    assert.equal(options.headers["Content-Type"], undefined);
    return {ok: false, status: 404, json: async () => ({error: "Snapshot not found."})};
  });
  await assert.rejects(api("/api/memory/snapshots/missing"), /Snapshot not found\./);
});

test("non-JSON service failures retain HTTP status", async () => {
  const api = createApi({token: "local-token"}, async () => ({ok: false, status: 503, json: async () => { throw new SyntaxError("not json"); }}));
  await assert.rejects(api("/api/jobs"), /Request failed \(503\)/);
});

test("authentication is never sent to a remote URL", async () => {
  let requests = 0;
  const api = createApi({token: "local-token"}, async () => { requests++; });
  for (const path of ["https://example.com/api/jobs", "//example.com/api/jobs", "api/jobs", "/assets/index.js"]) {
    await assert.rejects(api(path), /Only local API paths/);
  }
  assert.equal(requests, 0);
});

test("abort errors remain recognizable by feature disposal", async () => {
  const api = createApi({token: "local-token"}, async () => { throw new DOMException("Feature closed", "AbortError"); });
  await assert.rejects(api("/api/jobs"), {name: "AbortError"});
});
