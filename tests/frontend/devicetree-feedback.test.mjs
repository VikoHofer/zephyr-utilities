import test from "node:test";
import assert from "node:assert/strict";
import {announceResult} from "../../src/zephyr_utilities/features/devicetree/web/view-feedback.js";

function feedback(bounds = {top: 20, bottom: 40, left: 0, right: 400}) {
  return {isConnected: true, textContent: "Draft updated.",
    ownerDocument: {defaultView: {innerHeight: 800, innerWidth: 600}},
    getBoundingClientRect: () => bounds, getClientRects: () => [bounds], closest: () => null,
    focus() { assert.fail("Feedback must not move focus"); },
    scrollIntoView() { assert.fail("Feedback must not scroll"); }};
}

test("fully visible feedback stays inline without a duplicate notification", () => {
  const element = feedback({top: 0, bottom: 800, left: 0, right: 600});
  announceResult(element, () => assert.fail("Visible feedback needs no notification"));
});

test("offscreen or clipped feedback announces once without moving focus or scrolling", () => {
  for (const bounds of [{top: 820, bottom: 860, left: 0, right: 400},
    {top: -10, bottom: 20, left: 0, right: 400}, {top: 0, bottom: 40, left: -1, right: 400},
    {top: 0, bottom: 40, left: 400, right: 601}]) {
    const calls = []; announceResult(feedback(bounds), (...args) => calls.push(args));
    assert.deepEqual(calls, [["Draft updated.", false]]);
  }
});

test("concise supplied messages and error severity reach the notification", () => {
  const calls = [];
  announceResult(feedback({top: 820, bottom: 860, left: 0, right: 400}), (...args) => calls.push(args),
    {message: "Invalid pin. Choose an unreserved pin.", error: true});
  assert.deepEqual(calls, [["Invalid pin. Choose an unreserved pin.", true]]);
});

test("hidden, detached and unrendered feedback cannot publish notifications", () => {
  for (const overrides of [{hidden: true}, {isConnected: false}, {closest: () => ({hidden: true})},
    {getClientRects: () => []}]) {
    announceResult({...feedback(), ...overrides}, () => assert.fail("Hidden feedback must stay silent"));
  }
  announceResult(null, () => assert.fail("Missing feedback must stay silent"));
});

test("empty feedback and an unavailable notification callback are harmless", () => {
  const element = feedback({top: 820, bottom: 860, left: 0, right: 400});
  announceResult({...element, textContent: "  "}, () => assert.fail("Empty feedback must stay silent"));
  announceResult(element);
});
