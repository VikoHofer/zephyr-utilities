import {createDraftObservationSession} from "./draft-observation.js";
import {bindingEnumValue, bindingValue, unsignedCells} from "./bindings.js";

export function createPeripheralSession(options) {
  return createDraftObservationSession({...options, route: "peripherals", stateKey: "peripheralView",
    validResponse: (data) => ["nodes", "targets", "controllers", "gpio_groups", "gpio_providers", "group_options"]
      .every((key) => Array.isArray(data[key])), labels: {
      changed: "The draft or context changed. Load peripherals for the current source.",
      cancelled: "Peripheral inspection cancelled. The working draft is unchanged.",
      refreshing: "The overlay draft was updated. Refreshing its peripheral topology; Save remains a separate action.",
      blocked: "Finish or reconcile the draft request before loading peripherals. Incomplete source stays editable in the source panel.",
      mismatch: "The peripheral response does not match the current draft. Load peripherals again.",
      updated: "Draft updated. Peripheral topology now describes this source revision. Save remains a separate action.",
      loaded: "Local peripheral topology loaded. No devices, defaults or pin assignments have been added.",
      rejected: "The draft command was not accepted. Reconcile the draft or reload peripherals.",
      pending: "Draft reconciled. Sync retained source before loading peripherals.",
      reconciled: "Draft reconciled. Load peripherals again before applying a change.",
    }});
}

export function peripheralAddress(value, parent, excludeId) {
  const values = unsignedCells(value);
  if (values.length !== 1) throw new Error("Enter one address or chip-select number.");
  const address = values[0];
  if (parent?.kind === "i2c" && (address < 0x08 || address > 0x77)) throw new Error("Use a non-reserved 7-bit I2C address from 0x08 to 0x77.");
  if (parent?.children?.some((child) => child.node_id !== excludeId && child.address === address)) throw new Error("Another local device already uses this address or chip select, including disabled devices.");
  if (parent?.kind === "spi" && Array.isArray(parent.chip_selects) && !parent.chip_selects.includes(address)) throw new Error("Choose an index from the existing local cs-gpios array.");
  return address;
}

export function peripheralName(value) {
  if (!/^[A-Za-z][A-Za-z0-9,._+\-]{0,62}$/.test(value)) {
    throw new Error("Enter a node name of up to 63 letters, numbers, commas, periods, underscores, plus signs or hyphens, beginning with a letter. The address suffix is added separately.");
  }
  return value;
}

export function peripheralLabel(value) {
  if (value && !/^[A-Za-z_][A-Za-z_0-9]*$/.test(value)) throw new Error("Use letters, numbers and underscores for a node label, beginning with a letter or underscore.");
  return value;
}

export function peripheralFieldValue(property, value) {
  const parsed = bindingValue(property.type, value);
  return Array.isArray(property.enum) ? bindingEnumValue(property, parsed) : parsed;
}

export function topologyRows(data) {
  return [...data.controllers, ...data.gpio_groups].flatMap((parent) => [
    {key: parent.path || parent.name, parent: null, node: parent},
    ...(parent.children || []).map((node) => ({key: node.path || `${parent.path || parent.name}/${node.name}`, parent, node})),
  ]);
}
