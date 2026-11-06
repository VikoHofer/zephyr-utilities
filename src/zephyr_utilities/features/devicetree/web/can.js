import {createDraftObservationSession} from "./draft-observation.js";
import {unsignedCells} from "./bindings.js";

export function createCANSession(options) {
  return createDraftObservationSession({...options, route: "can", stateKey: "canView",
    validResponse: (data) => ["nodes", "targets", "controllers", "transceivers"].every((key) => Array.isArray(data[key])), labels: {
      changed: "The draft or context changed. Previous CAN details remain visible; load CAN controllers for the current source.",
      cancelled: "CAN inspection cancelled. The working draft is unchanged.",
      refreshing: "The overlay draft was updated. Refreshing CAN controllers; Save remains separate.",
      blocked: "Finish or reconcile the draft request before loading CAN controllers. Repair incomplete source in the source panel.",
      mismatch: "The CAN response does not match the current draft. Load CAN controllers again.",
      updated: "Draft updated. CAN details now describe this revision. Save remains separate.",
      loaded: "Local CAN controllers loaded. No rates, transceivers or defaults have been added.",
      rejected: "The CAN command was not accepted. Reconcile the draft or reload CAN controllers.",
      pending: "Draft reconciled. Sync retained source before loading CAN controllers.",
      reconciled: "Draft reconciled. Load CAN controllers again before applying a change.",
    }});
}

function integer(value, title, minimum, maximum) {
  let values;
  try { values = unsignedCells(String(value)); } catch { /* Report this field's exact range below. */ }
  if (values?.length !== 1 || values[0] < minimum || values[0] > maximum) {
    throw new Error(`Enter one unsigned integer from ${minimum} to ${maximum} for ${title}.`);
  }
  return values[0];
}

export function canValues(controller, selected) {
  if (controller?.editable !== true) throw new Error(controller?.reason || "Choose a supported local CAN controller.");
  const entries = Object.entries(selected);
  if (!entries.length || entries.some(([name]) => !["bitrate", "sample-point", "phys"].includes(name))) {
    throw new Error("Select at least one supported CAN field to set in this action.");
  }
  const values = Object.fromEntries(entries.map(([name, value]) => {
    const field = controller.fields?.[name];
    if (field?.editable !== true) throw new Error(field?.reason || `${name} requires binding or source editing.`);
    if (name === "phys") {
      const provider = field.providers?.find((item) => item.supported === true && item.reference === value);
      if (!provider) throw new Error("Choose a supported existing local active CAN transceiver.");
      return [name, provider.reference];
    }
    const result = name === "bitrate" ? integer(value, "nominal CAN bitrate", 1, 1000000)
      : integer(value, "CAN sample point", 0, 999);
    if (Array.isArray(field.enum) && !field.enum.includes(result)) throw new Error(`${name} must match an allowed local binding value.`);
    if (Object.hasOwn(field, "const") && result !== field.const) throw new Error(`${name} must match the local binding constant ${field.const}.`);
    return [name, result];
  }));
  const limits = Object.hasOwn(values, "phys")
    ? controller.fields.phys.providers.find((provider) => provider.reference === values.phys) : controller.limits;
  const within = (value) => !Number.isInteger(value)
    || ((!Number.isInteger(limits?.min_bitrate) || value >= limits.min_bitrate)
      && (!Number.isInteger(limits?.max_bitrate) || value <= limits.max_bitrate));
  if (Object.hasOwn(values, "bitrate") || Object.hasOwn(values, "phys")) {
    const nominal = values.bitrate ?? controller.nominal_rate?.value;
    if (!within(nominal)) throw new Error("The nominal bitrate lies outside the effective local transceiver's explicit limits.");
    if (Object.hasOwn(values, "phys") && !within(controller.data_rate?.value)) {
      throw new Error("The preserved CAN FD data rate lies outside the selected transceiver's explicit limits.");
    }
  }
  return values;
}

export function canRows(data) {
  return [...data.controllers.map((node) => ({key: "controller:" + (node.path || node.name), kind: "controller", node})),
    ...data.transceivers.map((node) => ({key: "transceiver:" + (node.path || node.reference || node.name), kind: "transceiver", node}))];
}
