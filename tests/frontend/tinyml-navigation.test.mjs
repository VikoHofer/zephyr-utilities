import test from "node:test";
import assert from "node:assert/strict";
import {createTinyMLNavigation} from "../../src/zephyr_utilities/features/tinyml/web/index.js";

function harness() {
  const chooser = {hidden: false}, drawing = {hidden: true}, opened = [];
  function open(kind) {
    const instance = {kind, views: [], closed: 0, navigate(view) { this.views.push(view); }, dispose() { this.closed++; }};
    opened.push(instance); return instance;
  }
  const navigation = createTinyMLNavigation({chooser, drawing,
    openChooser: () => open("chooser"), openDrawing: () => open("drawing")});
  return {navigation, chooser, drawing, opened};
}

test("drawing and chooser navigation dispose prior view resources and retain the shared feature", () => {
  const app = harness();
  app.navigation.navigate("overview"); app.navigation.navigate("draw");
  assert.equal(app.opened[0].closed, 1);
  assert.equal(app.chooser.hidden, true); assert.equal(app.drawing.hidden, false);
  app.navigation.navigate("draw");
  assert.equal(app.opened.length, 2);
  assert.deepEqual(app.opened[1].views, ["draw", "draw"]);
  app.navigation.navigate("overview");
  assert.equal(app.opened[1].closed, 1); assert.equal(app.opened[2].kind, "chooser");
  assert.equal(app.chooser.hidden, false); assert.equal(app.drawing.hidden, true);
  app.navigation.dispose(); app.navigation.dispose(); app.navigation.navigate("draw");
  assert.equal(app.opened[2].closed, 1); assert.equal(app.opened.length, 3);
});

test("unknown TinyML views retain the ordinary demo chooser", () => {
  const app = harness(); app.navigation.navigate("unknown");
  assert.deepEqual(app.opened[0].views, ["overview"]);
  app.navigation.dispose();
});
