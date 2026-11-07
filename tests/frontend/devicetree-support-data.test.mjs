import test from "node:test";
import assert from "node:assert/strict";
import {filterSupportedBoards, supportCatalog} from "../../src/zephyr_utilities/features/devicetree/web/support-data.js";

test("support search matches exact targets, chip names and functions without case sensitivity", () => {
  assert.deepEqual(filterSupportedBoards("  NORDIC nRF54L15 SPI  ").map((item) => item.target),
    ["nrf54l15dk/nrf54l15/cpuapp"]);
  assert.deepEqual(filterSupportedBoards("nucleo_g071rb/stm32g071xx").map((item) => item.name), ["Nucleo G071RB"]);
  assert.deepEqual(filterSupportedBoards("nRF9160dk@0.7.0/nrf9160").map((item) => item.soc), ["nRF9160"]);
});

test("support search combines vendor and text, preserves the catalogue, and handles empty results", () => {
  const before = structuredClone(supportCatalog);
  const all = filterSupportedBoards();
  assert.equal(all.length, supportCatalog.verifiedBoards.length);
  assert.notEqual(all, supportCatalog.verifiedBoards);
  assert.ok(filterSupportedBoards("", "stm32").every((item) => item.vendor === "STM32"));
  assert.ok(filterSupportedBoards("", "Nordic").every((item) => item.vendor === "Nordic"));
  assert.deepEqual(filterSupportedBoards("nrf54l15", "STM32"), []);
  assert.deepEqual(filterSupportedBoards("missing board"), []);
  assert.deepEqual(filterSupportedBoards(".*["), [], "search text is literal, not a regular expression");
  assert.deepEqual(filterSupportedBoards("", "unknown vendor"), []);
  assert.deepEqual(supportCatalog, before);
});

test("verified workflows retain evidence limits instead of promoting synthetic or blocked targets", () => {
  const boards = supportCatalog.verifiedBoards;
  assert.equal(new Set(boards.map((item) => item.target)).size, boards.length);
  for (const entry of boards) {
    assert.ok(entry.name && entry.vendor && entry.soc && entry.target && entry.verification && entry.build && entry.evidence);
    assert.ok(entry.features.length && entry.notes instanceof Array);
  }
  for (const target of ["nrf54l05", "nrf54l10", "nrf54h20", "nrf52810", "stm32f103", "cpunet", "/ns"]) {
    assert.equal(boards.some((item) => item.target.includes(target)), false, `${target} is not a verified board workflow`);
  }
  assert.match(boards.find((item) => item.soc === "STM32F446RE").verification, /I2C route was retained unchanged/);
  assert.match(boards.find((item) => item.soc === "STM32L476RG").verification, /SPI route was retained unchanged/);
  assert.match(boards.find((item) => item.soc === "nRF52840").build, /Not run/);
  assert.match(boards.find((item) => item.soc === "nRF54L15").notes.join(" "), /native-domain/);
  assert.match(supportCatalog.evidenceNotice, /No physical hardware operation was validated/);
});
