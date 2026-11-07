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

test("review completion returns focus to its re-enabled trigger without scrolling", async () => {
  const app = fixture(); const confirm = {parentNode: app.root, dataset: {focusKey: "confirm-save"}};
  app.document.activeElement = confirm; app.original.disabled = true;
  let finish; const waiting = new Promise((resolve) => { finish = resolve; });
  const running = retainActionFocus(app.root, () => waiting, () => true, undefined, {returnTo: app.original});
  app.callbacks.get("focusin")({target: confirm});
  app.document.activeElement = app.document.body; app.callbacks.get("focusin")({target: app.document.body});
  app.original.disabled = false; finish(true);
  assert.equal(await running, true); assert.equal(app.document.activeElement, app.original);
  assert.deepEqual(app.original.options, {preventScroll: true}); assert.equal(app.callbacks.size, 0);
});

test("review completion restores an equivalent replaced trigger and leaves a focused trigger alone", async () => {
  const app = fixture(); const confirm = {parentNode: app.root}; app.document.activeElement = confirm; let next;
  await retainActionFocus(app.root, () => { next = app.replace(); }, () => true, undefined, {returnTo: app.original});
  assert.equal(app.document.activeElement, next); assert.deepEqual(next.options, {preventScroll: true});
  next.options = undefined;
  await retainActionFocus(app.root, () => true, () => true, undefined, {returnTo: next});
  assert.equal(next.options, undefined, "a focused surviving trigger is not refocused");
});

for (const event of ["focusin", "pointerdown"]) test(`later ${event} suppresses review return focus`, async () => {
  const app = fixture(); app.document.activeElement = {parentNode: app.root};
  await retainActionFocus(app.root, () => {
    app.callbacks.get(event)({target: {}}); app.document.activeElement = app.document.body;
  }, () => true, undefined, {returnTo: app.original});
  assert.equal(app.document.activeElement, app.document.body); assert.equal(app.callbacks.size, 0);
});

test("review navigation, disposal and hidden or disabled triggers suppress restoration", async () => {
  for (const state of ["navigation", "disposal", "hidden", "disabled", "unrendered"]) {
    const app = fixture(); const lifetime = new AbortController(); app.document.activeElement = {parentNode: app.root};
    await retainActionFocus(app.root, () => {
      app.document.activeElement = app.document.body;
      if (state === "disposal") lifetime.abort();
      if (state === "hidden") app.original.closest = (selector) => selector === "[hidden]" ? {} : null;
      if (state === "disabled") app.original.disabled = true;
      if (state === "unrendered") app.original.getClientRects = () => [];
    }, () => state !== "navigation", lifetime.signal, {returnTo: app.original});
    assert.equal(app.document.activeElement, app.document.body, state); assert.equal(app.callbacks.size, 0, state);
  }
});

test("a completed action that hides its retry control returns to the named primary action", async () => {
  const app = fixture(); const save = {parentNode: app.root,
    focus(options) { app.document.activeElement = this; this.options = options; }};
  await retainActionFocus(app.root, () => {
    app.original.closest = (selector) => selector === "[hidden]" ? {} : null;
    app.document.activeElement = app.document.body;
  }, () => true, undefined, {fallbackTo: save});
  assert.equal(app.document.activeElement, save); assert.deepEqual(save.options, {preventScroll: true});
});

test("fallback focus leaves a surviving retry control or a later user action alone", async () => {
  for (const moved of [false, true]) {
    const app = fixture(); const save = {parentNode: app.root,
      focus() { app.document.activeElement = this; }};
    await retainActionFocus(app.root, () => {
      if (moved) { app.callbacks.get("pointerdown")({target: {}}); app.document.activeElement = app.document.body; }
    }, () => true, undefined, {fallbackTo: save});
    assert.equal(app.document.activeElement, moved ? app.document.body : app.original);
  }
});
