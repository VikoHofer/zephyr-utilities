import test from "node:test";
import assert from "node:assert/strict";
import {retainActionFocus} from "../../src/zephyr_utilities/features/devicetree/web/view-focus.js";

function fixture() {
  const callbacks = new Map(); const form = {children: [{textContent: "Assignment"}]};
  const document = {body: {}, hasFocus: () => true,
    addEventListener(type, callback, {signal}) {
      callbacks.set(type, callback); signal.addEventListener("abort", () => callbacks.delete(type));
    }};
  let controls = [];
  const root = {ownerDocument: document, querySelectorAll: () => controls};
  const control = () => ({parentNode: root, dataset: {focusKey: "apply"},
    closest: (selector) => selector === "form" ? form : null,
    focus(options) { document.activeElement = this; this.options = options; }});
  const original = control(); controls = [original]; document.activeElement = original;
  function replace() {
    original.parentNode = null; const next = control(); controls = [next]; document.activeElement = document.body; return next;
  }
  return {root, document, callbacks, original, replace};
}

test("an explicit action restores the equivalent control without scrolling after replacement", async () => {
  const app = fixture(); let next;
  assert.equal(await retainActionFocus(app.root, () => { next = app.replace(); return true; }, () => true), true);
  assert.equal(app.document.activeElement, next); assert.deepEqual(next.options, {preventScroll: true});
  assert.equal(app.callbacks.size, 0);
});

test("a temporarily disabled retained control regains focus when the action finishes", async () => {
  const app = fixture();
  await retainActionFocus(app.root, () => { app.document.activeElement = app.document.body; return true; }, () => true);
  assert.equal(app.document.activeElement, app.original); assert.deepEqual(app.original.options, {preventScroll: true});
});

for (const event of ["focusin", "pointerdown"]) test(`a later ${event} cancels restoration even if focus ends on the body`, async () => {
  const app = fixture();
  await retainActionFocus(app.root, () => {
    app.callbacks.get(event)({target: {}}); app.replace(); return true;
  }, () => true);
  assert.equal(app.document.activeElement, app.document.body); assert.equal(app.callbacks.size, 0);
});

test("navigation and an inactive document suppress explicit action restoration", async () => {
  for (const active of [true, false]) {
    const app = fixture(); app.document.hasFocus = () => active;
    await retainActionFocus(app.root, () => app.replace(), () => !active);
    assert.equal(app.document.activeElement, app.document.body); assert.equal(app.callbacks.size, 0);
  }
});

test("removed controls without an equivalent target do not focus an unrelated action", async () => {
  const app = fixture();
  await retainActionFocus(app.root, () => { app.replace().dataset.focusKey = "unrelated"; }, () => true);
  assert.equal(app.document.activeElement, app.document.body);
});

test("disposal removes pending action listeners immediately and suppresses late focus", async () => {
  const app = fixture(); const lifetime = new AbortController(); let finish;
  const waiting = new Promise((resolve) => { finish = resolve; });
  const running = retainActionFocus(app.root, () => waiting, () => true, lifetime.signal);
  assert.equal(app.callbacks.size, 2); lifetime.abort(); assert.equal(app.callbacks.size, 0);
  app.replace(); finish(true); await running;
  assert.equal(app.document.activeElement, app.document.body);
});
