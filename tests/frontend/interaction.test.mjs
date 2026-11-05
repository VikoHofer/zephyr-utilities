import test from "node:test";
import assert from "node:assert/strict";
import {revealResult} from "../../src/zephyr_utilities/web/shared/interaction.js";

function result(overrides = {}) {
  const calls = [];
  const attributes = new Map();
  const element = {isConnected: true, tabIndex: -1, parentElement: null,
    closest: () => null, getClientRects: () => [{}],
    hasAttribute: (name) => attributes.has(name), setAttribute: (name, value) => attributes.set(name, value),
    focus: (options) => calls.push(["focus", options]), scrollIntoView: (options) => calls.push(["scroll", options]),
    ...overrides};
  return {element, calls, attributes};
}

test("requested results become keyboard destinations without animation", () => {
  const item = result();
  assert.equal(revealResult(item.element), true);
  assert.equal(item.attributes.get("tabindex"), "-1");
  assert.equal(item.attributes.has("data-reveal-target"), true);
  assert.deepEqual(item.calls, [["focus", {preventScroll: true}], ["scroll", {behavior: "instant", block: "start", inline: "nearest"}]]);
});

test("hidden, disposed, and closed-dialog results never steal focus or scroll", () => {
  for (const overrides of [{isConnected: false}, {closest: () => ({hidden: true})}, {getClientRects: () => []}]) {
    const item = result(overrides);
    assert.equal(revealResult(item.element), false);
    assert.deepEqual(item.calls, []);
  }
  assert.equal(revealResult(null), false);
});

test("a requested nested result opens its disclosures before being revealed", () => {
  const outer = {tagName: "DETAILS", open: false};
  const inner = {tagName: "DETAILS", open: false, parentElement: outer};
  const item = result({parentElement: inner, getClientRects: () => inner.open && outer.open ? [{}] : []});
  assert.equal(revealResult(item.element), true);
  assert.equal(inner.open, true);
  assert.equal(outer.open, true);
});

test("an in-place action can retain keyboard focus while revealing nearby results", () => {
  const item = result();
  revealResult(item.element, {focus: false, block: "nearest"});
  assert.deepEqual(item.calls, [["scroll", {behavior: "instant", block: "nearest", inline: "nearest"}]]);
  assert.equal(item.attributes.size, 0);
});

test("existing keyboard tab order is preserved", () => {
  const item = result({tabIndex: 0});
  revealResult(item.element);
  assert.equal(item.attributes.size, 0);
});

test("explicitly focusable controls retain their existing focus styling", () => {
  const item = result({hasAttribute: (name) => name === "tabindex"});
  revealResult(item.element);
  assert.equal(item.attributes.size, 0, "only targets made focusable by reveal receive the content marker");
  assert.equal(item.calls[0][0], "focus");
});
