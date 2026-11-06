import {createDraftObservationSession} from "./draft-observation.js";
import {unsignedCells} from "./bindings.js";

export function createInterruptGPIOSession(options) {
  return createDraftObservationSession({...options, route: "interrupt-gpios", stateKey: "interruptGPIOView",
    validResponse: (data) => ["nodes", "targets", "providers", "consumers"].every((key) => Array.isArray(data[key])), labels: {
      changed: "The draft or context changed. Previous interrupt GPIO details remain visible; load interrupt GPIOs for the current source.",
      cancelled: "Interrupt GPIO inspection cancelled. The working draft is unchanged.",
      refreshing: "The overlay draft was updated. Refreshing interrupt GPIOs; Save remains separate.",
      blocked: "Finish or reconcile the draft request before loading interrupt GPIOs. Repair incomplete source in the source panel.",
      mismatch: "The interrupt GPIO response does not match the current draft. Load interrupt GPIOs again.",
      updated: "Draft updated. Interrupt GPIO details now describe this revision. Save remains separate.",
      loaded: "Local interrupt GPIOs loaded. No providers, pins or defaults have been added.",
      rejected: "The interrupt GPIO command was not accepted. Reconcile the draft or reload interrupt GPIOs.",
      pending: "Draft reconciled. Sync retained source before loading interrupt GPIOs.",
      reconciled: "Draft reconciled. Load interrupt GPIOs again before applying a change.",
    }});
}

function integer(value, title, maximum) {
  const values = unsignedCells(String(value));
  if (values.length !== 1 || values[0] > maximum) throw new Error(`Enter one unsigned integer from 0 to ${maximum} for ${title}.`);
  return values[0];
}

export function interruptGPIOFlags(value, polarity) {
  const flags = integer(value, "GPIO flags", 65535);
  if (!["0", "1"].includes(String(polarity))) throw new Error("Choose logical active high or active low.");
  return flags - flags % 2 + Number(polarity);
}

export function interruptGPIOEntry(provider, values) {
  if (provider?.supported !== true || provider?.cells?.join(",") !== "pin,flags") {
    throw new Error("Choose a supported local GPIO provider with the pin, flags cell layout.");
  }
  const pin = integer(values.pin, "GPIO pin", 31); const flags = integer(values.flags, "GPIO flags", 65535);
  if (Number.isInteger(provider.pin_count) && pin >= provider.pin_count) {
    throw new Error(`The pin must be below the locally declared pin count of ${provider.pin_count}.`);
  }
  if (Array.isArray(provider.reserved_ranges) && provider.reserved_ranges.some(([start, count]) => pin >= start && pin < start + count)) {
    throw new Error("This pin is reserved by the local GPIO provider declaration.");
  }
  return {reference: provider.reference, cells: [pin, flags]};
}

export function interruptGPIORows(data) {
  return data.consumers.map((node) => ({key: (node.path || node.name) + ":" + node.property, node}));
}
