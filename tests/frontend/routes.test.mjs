import test from "node:test";
import assert from "node:assert/strict";
import {parseRoute, routeHash, featureAssets, activeJobRoute, navigationEntries, activeNavigationEntry} from "../../src/zephyr_utilities/web/shared/routes.js";

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

test("Memory inspection links preserve the observation and encoded image name", () => {
  const view = `inspect/${"a".repeat(32)}/${encodeURIComponent("network / secondary")}`;
  assert.deepEqual(parseRoute(`#/memory/${view}`), {feature: "memory", view, path: `memory/${view}`});
  assert.equal(routeHash(`memory/${view}`), `#/memory/${view}`);
  assert.equal(parseRoute("#/memory/inspect/invalid").view, "inspect/invalid");
});

test("legacy inspection links move to Inspector without changing encoded identities", () => {
  const id = "a".repeat(32);
  for (const [old, current] of [
    ["projects/builds", "inspector/builds"],
    ["projects/builds/%2Fworkspace%2Fbuild", "inspector/builds/%2Fworkspace%2Fbuild"],
    [`devicetree/inspect/${id}/boot%20loader`, `inspector/inspect/${id}/boot%20loader`],
    ["devicetree/application/%2Fworkspace%2Fmy%20app", "inspector/application/%2Fworkspace%2Fmy%20app"],
  ]) assert.equal(routeHash(old), `#/${current}`);
  assert.equal(routeHash("devicetree"), "#/devicetree", "the planned visual editor keeps its own entry");
  assert.equal(routeHash("projects/create"), "#/projects/create");
});

const inspector = {id: "inspector", status: "available", entry_path: "/#/inspector/builds", navigation_group: "advanced", navigation: [
  {label: "Build outputs", view: "builds", match_views: ["builds"]},
  {label: "Sources and Device Tree", view: "sources", match_views: ["sources", "inspect", "application"]},
]};

test("sidebar entries stay feature-local and match nested destinations", () => {
  assert.deepEqual(navigationEntries(inspector).map((entry) => entry.path), ["inspector/builds", "inspector/sources"]);
  for (const path of ["inspector", "inspector/builds", "projects/builds/%2Fworkspace%2Fbuild"]) {
    assert.equal(activeNavigationEntry(inspector, parseRoute(path))?.view, "builds");
  }
  for (const path of ["inspector/sources", "inspector/inspect/abc/image", "devicetree/application/%2Fworkspace%2Fapp"]) {
    assert.equal(activeNavigationEntry(inspector, parseRoute(path))?.view, "sources");
  }
  assert.equal(activeNavigationEntry(inspector, parseRoute("inspector/builds-other")), null);
  assert.equal(activeNavigationEntry(inspector, parseRoute("projects/environment")), null);
});

test("the most specific declared view is active and planned tools have no executable children", () => {
  const feature = {...inspector, navigation: [
    {label: "Results", view: "results"}, {label: "History", view: "results/history"},
  ]};
  assert.equal(activeNavigationEntry(feature, parseRoute("inspector/results/history/run-1"))?.label, "History");
  assert.deepEqual(navigationEntries({...feature, status: "planned"}), []);
  assert.deepEqual(navigationEntries({...feature, navigation: [
    {label: "External", view: "https://outside.invalid"}, {label: "Traversal", view: "../memory"},
    {label: "Valid", view: "results", match_views: ["../memory", "results/nested"]},
  ]}), [{label: "Valid", view: "results", match_views: ["results", "results/nested"], path: "inspector/results"}]);
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
