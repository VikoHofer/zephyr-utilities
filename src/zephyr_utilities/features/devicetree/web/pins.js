import {createDraftObservationSession} from "./draft-observation.js";

export function createPinSession(options) {
  return createDraftObservationSession({...options, route: "pins", stateKey: "pinView",
    validResponse: (data) => typeof data.supported === "boolean"
      && ["pins", "controllers", "gpio", "gpio_flags", "diagnostics"].every((key) => Array.isArray(data[key])),
    labels: {
      changed: "The draft or context changed. Load pin assignments for the current source.",
      cancelled: "Pin inspection cancelled. The working draft is unchanged.",
      refreshing: "The overlay draft was updated. Refreshing pin assignments; Save remains a separate action.",
      blocked: "Finish or reconcile the draft request before loading pins. Incomplete source stays editable in the source panel.",
      mismatch: "The pin response does not match the current draft. Load pin assignments again.",
      updated: "Draft updated. Pin assignments now describe this source revision. Save remains a separate action.",
      loaded: "Pin inspection finished. No pins or controller defaults have been changed.",
      rejected: "The pin command was not accepted. Reconcile the draft or reload pins.",
      pending: "Draft reconciled. Sync retained source before loading pin assignments.",
      reconciled: "Draft reconciled. Load pin assignments again before applying a change.",
    }});
}

export const pinKey = (pin) => `${pin.port}:${pin.pin}`;
export const pinName = (pin) => pin.name || `P${pin.port}.${String(pin.pin).padStart(2, "0")}`;

export function selectedPin(pins, key) {
  const pin = pins.find((item) => pinKey(item) === key);
  if (!pin) throw new Error("Choose a pin established by the selected target evidence.");
  if (pin.editable === false) throw new Error(pin.reason || "This pin is unavailable.");
  return {port: pin.port, pin: pin.pin};
}

export function selectedFlags(flags, allowed) {
  const names = allowed.map((item) => typeof item === "string" ? item : item.name);
  if (!flags.length) throw new Error("Choose GPIO flags explicitly, including the intended active polarity.");
  if (flags.some((flag) => !names.includes(flag)) || new Set(flags).size !== flags.length) {
    throw new Error("Choose distinct GPIO flags from the observed symbolic constants.");
  }
  if (["GPIO_ACTIVE_LOW", "GPIO_ACTIVE_HIGH"].every((flag) => flags.includes(flag))) {
    throw new Error("Choose one GPIO polarity.");
  }
  if (["GPIO_PULL_UP", "GPIO_PULL_DOWN"].every((flag) => flags.includes(flag))) {
    throw new Error("Choose at most one GPIO pull direction.");
  }
  return flags;
}

export function gpioKey(row) { return `${row.path || row.name || row.node_id}|${row.property}|${row.index}`; }
