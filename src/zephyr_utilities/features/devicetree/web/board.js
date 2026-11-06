import {createDraftObservationSession} from "./draft-observation.js";
import {unsignedCells} from "./bindings.js";

export function createBoardSession({api, drafts, ...options}) {
  return createDraftObservationSession({...options, route: "board", stateKey: "boardView",
    api: async (...args) => {
      const data = await api(...args);
      return data && {...data, schema_id: data.observation_id};
    },
    drafts: {pending: () => drafts.pending(), reconcile: () => drafts.reconcile(),
      command: ({schema_id, ...command}) => drafts.command({...command, observation_id: schema_id})},
    validResponse: (data) => data.schema_version === 1 && data.board && typeof data.board === "object"
      && typeof data.supported === "boolean" && ["nodes", "controllers", "gpio_providers"].every((key) => Array.isArray(data[key]))
      && data.connection_options && typeof data.connection_options === "object" && !Array.isArray(data.connection_options), labels: {
      changed: "The draft or board configuration changed. Load board hardware again before adding a device.",
      cancelled: "Board discovery cancelled. The working draft is unchanged.",
      refreshing: "Device added to the draft. Refreshing board hardware…",
      blocked: "Open an application and overlay, and finish or reconcile the draft request before loading board hardware.",
      mismatch: "The board hardware response does not match this draft. Load board hardware again.",
      updated: "Device added to the draft and shown on its connection. Review and Save the overlay separately.",
      loaded: "Board hardware inspection complete.",
      rejected: "The device could not be added. Reconcile the draft or load board hardware again.",
      pending: "Draft reconciled. Sync retained source before loading board hardware.",
      reconciled: "Draft reconciled. Load board hardware again before adding a device.",
    }});
}

export function boardConnections(data, kind = "") {
  const nodes = [...data.controllers, ...data.gpio_providers]; const seen = new Set();
  return nodes.filter((node) => {
    if (seen.has(node.id)) return false;
    seen.add(node.id);
    return !kind || (data.connection_options[node.id] || []).some((option) => option.kind === kind);
  });
}

export function boardOption(data, controllerId, kind) {
  return data?.connection_options?.[controllerId]?.find((option) => option.kind === kind
    && (kind !== "i2c" || option.compatible === "bosch,bme280"));
}

function integer(value, title, minimum, maximum) {
  let values;
  try { values = unsignedCells(String(value)); } catch { /* Use the connection's bounds in the error. */ }
  if (values?.length !== 1 || values[0] < minimum || values[0] > maximum) {
    throw new Error(`Choose one ${title} from ${minimum} to ${maximum}.`);
  }
  return values[0];
}

export function boardConnection(data, controllerId, kind, values) {
  const controller = boardConnections(data).find((node) => node.id === controllerId);
  const option = boardOption(data, controllerId, kind);
  if (!data.supported || !controller || option?.addable !== true) {
    throw new Error(option?.reason || data.reason || "Choose an available connection for this device.");
  }
  if (typeof values.name !== "string" || !/^[A-Za-z_][A-Za-z_0-9-]{0,63}$/.test(values.name)) {
    throw new Error("Choose a node name of up to 64 letters, numbers, underscores or hyphens, beginning with a letter or underscore.");
  }
  if (typeof values.label !== "string" || values.label && !/^[A-Za-z_][A-Za-z_0-9]{0,63}$/.test(values.label)) {
    throw new Error("Choose a node label of up to 64 letters, numbers or underscores, or leave it empty.");
  }
  if (values.label && data.nodes.some((node) => node.labels?.includes(values.label))) throw new Error("This label already belongs to a board or overlay node. Choose another label.");
  const identity = {name: values.name, label: values.label};
  const availablePath = (path) => {
    if (data.nodes.some((node) => node.path === path)) throw new Error("This device node name is already used on the connection. Choose another name under Node name and label.");
  };
  if (kind === "i2c") {
    const address = integer(values.address, "I2C address", option.address_min ?? 8, option.address_max ?? 119);
    if (option.occupied_addresses?.includes(address)) throw new Error("This I2C address is already used on the selected connection. Choose the sensor's other address or another controller.");
    if (option.enable_required && values.enable !== true) throw new Error("Select Enable this I2C controller to use the existing board routing for this sensor.");
    availablePath(controller.path + "/" + identity.name + "@" + address.toString(16));
    return {kind, controller_id: controllerId, compatible: "bosch,bme280", address, ...identity, enable: values.enable === true};
  }
  if (kind === "gpio-led") {
    if (!Number.isInteger(option.pin_max)) throw new Error("The GPIO pin range is unknown for this connection.");
    const pin = integer(values.pin, "GPIO pin", option.pin_min ?? 0, option.pin_max);
    const flags = integer(values.flags, "GPIO flags", option.flags_min ?? 0, option.flags_max ?? 65535);
    if (option.occupied_pins?.includes(pin)) throw new Error("This GPIO pin is already used by an observed device or pin assignment. Choose another pin.");
    if (option.reserved_ranges?.some(([start, count]) => pin >= start && pin < start + count)) throw new Error("This GPIO pin is reserved. Choose another pin.");
    availablePath("/app-leds/" + identity.name);
    return {kind, controller_id: controllerId, pin, flags, ...identity};
  }
  throw new Error("Choose a BME280 sensor or LED to connect.");
}
