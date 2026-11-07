import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspaceViews} from "../../src/zephyr_utilities/features/devicetree/web/workspace-views.js";

function harness() {
  const calls = [];
  const editorRoot = {hidden: true};
  const supportRoot = {hidden: true};
  const views = createWorkspaceViews({editorRoot, supportRoot,
    mountEditor() {
      calls.push("mount editor");
      return {navigate(view) { calls.push(`editor ${view}`); return true; }, dispose() { calls.push("dispose editor"); }};
    },
    mountSupport() {
      calls.push("mount support");
      return {dispose() { calls.push("dispose support"); }};
    },
  });
  return {views, calls, editorRoot, supportRoot};
}

test("direct support navigation needs no editor session or project discovery", () => {
  const app = harness();
  app.views.navigate("support");
  assert.deepEqual(app.calls, ["mount support"]);
  assert.equal(app.editorRoot.hidden, true);
  assert.equal(app.supportRoot.hidden, false);
  app.views.navigate("support");
  assert.deepEqual(app.calls, ["mount support"]);
  app.views.dispose();
});

test("switching to support disposes editor activity and returning mounts a fresh session", () => {
  const app = harness();
  app.views.navigate("editor");
  app.views.navigate("support");
  assert.deepEqual(app.calls, ["mount editor", "editor editor", "dispose editor", "mount support"]);
  app.views.navigate("editor");
  assert.deepEqual(app.calls.slice(-2), ["mount editor", "editor editor"]);
  assert.equal(app.editorRoot.hidden, false);
  assert.equal(app.supportRoot.hidden, true);
  app.views.navigate("editor");
  assert.equal(app.calls.filter((value) => value === "mount editor").length, 2);
  app.views.dispose();
});

test("editor aliases and invalid views still use existing editor route validation", () => {
  const app = harness();
  app.views.navigate("");
  app.views.navigate("missing");
  assert.deepEqual(app.calls, ["mount editor", "editor ", "editor missing"]);
  app.views.dispose();
});

test("feature disposal releases both subviews once and prevents late navigation", () => {
  const app = harness();
  app.views.navigate("support");
  app.views.navigate("editor");
  app.views.dispose();
  assert.deepEqual(app.calls.slice(-2), ["dispose editor", "dispose support"]);
  const count = app.calls.length;
  app.views.dispose();
  assert.equal(app.views.navigate("support"), false);
  assert.equal(app.calls.length, count);
});
