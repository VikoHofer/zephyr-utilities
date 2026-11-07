import test from "node:test";
import assert from "node:assert/strict";
import {mountSupportPanel} from "../../src/zephyr_utilities/features/devicetree/web/support-view.js";

class Element extends EventTarget {
  constructor(tag, document) {
    super();
    this.tagName = tag; this.ownerDocument = document; this.children = []; this.dataset = {};
    this.attributes = {}; this.value = ""; this.hidden = false; this.className = ""; this.ownText = "";
    this.classList = {add: (name) => { this.className = `${this.className} ${name}`.trim(); }};
  }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
  get firstElementChild() { return this.children[0] || null; }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.ownText = ""; this.children = children; }
  focus() { this.ownerDocument.activeElement = this; }
}

function all(root, predicate) {
  return [root, ...root.children.flatMap((child) => all(child, () => true))].filter(predicate);
}
function byClass(root, className) { return all(root, (element) => element.className.split(" ").includes(className)); }
function harness(state = {}) {
  const document = {activeElement: null};
  document.createElement = (tag) => new Element(tag, document);
  const root = document.createElement("section");
  const reveals = [];
  const context = {state, api() { throw new Error("Support information must work without API calls"); },
    reveal(element, options) { reveals.push({element, options}); element.focus(); }};
  const panel = mountSupportPanel(root, context);
  return {root, state, panel, document, reveals, search: all(root, (element) => element.tagName === "input")[0],
    vendor: all(root, (element) => element.tagName === "select")[0],
    clear: all(root, (element) => element.tagName === "button" && element.textContent === "Clear filters")[0],
    count: all(root, (element) => element.attributes.role === "status")[0],
    cards: () => byClass(root, "dte-support-board"),
    empty: byClass(root, "dte-support-empty")[0]};
}

test("support is available offline and separates board, family, unavailable and contribution evidence", () => {
  const app = harness();
  assert.equal(all(app.root, (element) => element.tagName === "h1")[0].textContent, "Supported boards");
  assert.deepEqual(all(app.root, (element) => element.tagName === "h2").map((element) => element.textContent),
    ["Automatic family coverage", "Verified board workflows", "Under construction", "Contribute support"]);
  const nrf54 = app.cards().find((card) => card.textContent.includes("nrf54l15dk/nrf54l15/cpuapp"));
  assert.ok(nrf54);
  assert.match(nrf54.textContent, /GPIO · UART · I2C · SPI/);
  assert.match(nrf54.textContent, /Configure \/ build: Passed/);
  assert.match(nrf54.textContent, /Limits and verification/);
  assert.equal(app.count.attributes["aria-live"], "polite");
  assert.equal(app.empty.hidden, true);
  assert.equal(app.reveals.length, 0);
  app.panel.dispose();
});

test("combined search and vendor filtering retain controls, focus and state with visible empty feedback", () => {
  const app = harness(); const search = app.search;
  search.focus(); search.value = "  nrf54l15  "; search.dispatchEvent(new Event("input"));
  assert.equal(app.cards().length, 1);
  assert.equal(app.document.activeElement, search);
  assert.equal(all(app.root, (element) => element.tagName === "input")[0], search);
  assert.match(app.count.textContent, /^1 of /);
  assert.equal(app.state.supportFilters.query, "  nrf54l15  ");
  app.vendor.focus(); app.vendor.value = "STM32"; app.vendor.dispatchEvent(new Event("change"));
  assert.equal(app.cards().length, 0); assert.equal(app.empty.hidden, false);
  assert.match(app.empty.textContent, /automatic adapter/);
  assert.equal(app.document.activeElement, app.vendor);
  assert.equal(app.state.supportFilters.vendor, "STM32");
  app.clear.focus(); app.clear.dispatchEvent(new Event("click"));
  assert.equal(app.empty.hidden, true); assert.ok(app.cards().length > 1);
  assert.equal(app.search.value, ""); assert.equal(app.vendor.value, "all");
  assert.equal(app.document.activeElement, app.clear);
  assert.equal(app.reveals.length, 0);
  app.panel.dispose();
});

test("header shortcuts reveal only their requested section heading and dispose cleanly", () => {
  const app = harness();
  const labels = ["Automatic coverage", "Under construction", "Contribute support"];
  const headings = ["Automatic family coverage", "Under construction", "Contribute support"];
  const buttons = labels.map((label) => all(app.root, (element) => element.tagName === "button" && element.textContent === label)[0]);
  for (const [index, button] of buttons.entries()) {
    button.dispatchEvent(new Event("click"));
    assert.equal(app.reveals.length, index + 1);
    assert.equal(app.reveals[index].element.tagName, "h2");
    assert.equal(app.reveals[index].element.textContent, headings[index]);
    assert.deepEqual(app.reveals[index].options, {block: "start"});
    assert.equal(app.document.activeElement, app.reveals[index].element);
  }
  app.panel.dispose();
  buttons[0].dispatchEvent(new Event("click"));
  assert.equal(app.reveals.length, 3);
});

test("leaving disposes listeners and reopening retains the filters", () => {
  const app = harness();
  app.search.value = "nrf9161"; app.search.dispatchEvent(new Event("input"));
  app.panel.dispose();
  app.search.value = "nucleo"; app.search.dispatchEvent(new Event("input"));
  assert.equal(app.state.supportFilters.query, "nrf9161");
  const reopened = harness(app.state);
  assert.equal(reopened.search.value, "nrf9161"); assert.equal(reopened.cards().length, 1);
  assert.match(reopened.cards()[0].textContent, /nrf9161dk\/nrf9161/);
  reopened.panel.dispose();
});

test("stale presentation filters fall back to the available vendors", () => {
  const app = harness({supportFilters: {query: null, vendor: "retired"}});
  assert.equal(app.search.value, ""); assert.equal(app.vendor.value, "all");
  assert.equal(app.empty.hidden, true);
  app.panel.dispose();
});
