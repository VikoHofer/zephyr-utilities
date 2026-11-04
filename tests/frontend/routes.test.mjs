import test from "node:test";
import assert from "node:assert/strict";
import {parseRoute, routeHash, featureAssets, activeJobRoute} from "../../src/zephyr_utilities/web/shared/routes.js";

test("empty startup goes to Home and explicit memory entry goes to Overview", () => {
  for (const hash of ["", "#", "#/", "/"]) assert.equal(routeHash(hash), "#/home");
  for (const hash of ["#/memory", "/#/memory/overview", "memory/overview", "/memory/"]) {
    assert.equal(routeHash(hash), "#/memory/overview");
  }
});

test("old memory bookmarks preserve their destination", () => {
  for (const view of ["overview", "explorer", "compare", "snapshots"]) {
    assert.deepEqual(parseRoute(`#${view}`), {feature: "memory", view, path: `memory/${view}`});
  }
  assert.equal(routeHash("#/memory/missing"), "#/memory/overview");
});

test("routing supports new feature IDs and nested views without a shell edit", () => {
  assert.deepEqual(parseRoute("#/example/results/run-1"), {feature: "example", view: "results/run-1", path: "example/results/run-1"});
  assert.equal(routeHash("/#/devicetree"), "#/devicetree");
  assert.equal(routeHash("/#/tinyml"), "#/tinyml");
});

const memory = {id: "memory", status: "available", frontend: {module: "/assets/memory/index.js", template: "/assets/memory/view.html", stylesheet: "/assets/memory/style.css"}};

test("only declared feature-local assets are loadable", () => {
  assert.deepEqual(featureAssets(memory), memory.frontend);
  for (const module of ["https://example.com/index.js", "//example.com/index.js", "/assets/other/index.js", "/assets/memory/../index.js", "/assets/memory/%2e%2e/index.js", "/assets/memory/index.js?remote=1"]) {
    assert.throws(() => featureAssets({...memory, frontend: {...memory.frontend, module}}), /Invalid local module/);
  }
});

test("planned tools cannot mount executable assets", () => {
  assert.throws(() => featureAssets({...memory, status: "planned"}), /not available yet/);
  assert.throws(() => featureAssets({...memory, frontend: null}), /not available yet/);
});

test("the shared job link opens the registered owner instead of assuming Memory", () => {
  const features = [{id: "example", status: "available", entry_path: "/#/example/results"}, {id: "planned", status: "planned"}];
  assert.equal(activeJobRoute([{feature: "example"}], features), "/#/example/results");
  assert.equal(activeJobRoute([{feature: "unknown"}, {feature: "example"}], features), "/#/example/results");
  assert.equal(activeJobRoute([{feature: "planned"}], features), "home");
  assert.equal(activeJobRoute([], features), "home");
});

test("a job can return to its owning feature's operation view", () => {
  const features = [{id: "projects", status: "available", entry_path: "/#/projects/environment"}];
  assert.equal(activeJobRoute([{feature: "projects", metadata: {view: "create"}}], features), "projects/create");
  for (const view of ["../memory", "https://outside.invalid", "/home", "create?x=1"]) {
    assert.equal(activeJobRoute([{feature: "projects", metadata: {view}}], features), "/#/projects/environment");
  }
});
