/** Explicit, revision-bound binding inspection on the canonical draft command path. */
import {createDraftObservationSession} from "./draft-observation.js";

export const bindingTypes = new Set(["boolean", "int", "string", "string-array", "array", "uint8-array",
  "phandle", "path", "phandles", "phandle-array"]);

export function unsignedCells(text, maximum = 0xffffffff) {
  const values = String(text).trim().split(/[\s,]+/).filter(Boolean);
  if (!values.length || values.length > 64 || values.some((value) => !/^(?:0[xX][\da-fA-F]+|\d+)$/.test(value)
      || !Number.isSafeInteger(Number(value)) || Number(value) > maximum)) {
    throw new Error(`Enter one to 64 integers from 0 to ${maximum}, separated by spaces. Decimal and 0x hexadecimal are accepted.`);
  }
  return values.map(Number);
}

export function localBindingValue(property) {
  if (!property?.local) return undefined;
  if (Object.hasOwn(property.local, "parsed")) return property.local.parsed;
  const value = property.local.value;
  if (!value) return undefined;
  if (property.type === "boolean") return true;
  if (property.type === "int") return Array.isArray(value.value) ? value.value[0] : value.value;
  return value.value;
}

export function bindingEnumValue(property, value) {
  const list = ["array", "string-array"].includes(property.type);
  const values = list ? value : [value];
  if (!Array.isArray(values) || !values.length || values.length > 64
      || values.some((item) => !property.enum.some((allowed) => allowed === item))) {
    throw new Error(list ? "Choose one to 64 entries from the binding enumeration."
      : "Choose a value from the binding enumeration.");
  }
  return value;
}

export function bindingValue(type, value) {
  if (type === "boolean") {
    if (typeof value !== "boolean") throw new Error("Choose whether this boolean property is present.");
    return value;
  }
  if (type === "int") {
    const cells = unsignedCells(value);
    if (cells.length !== 1) throw new Error("Enter one unsigned 32-bit integer.");
    return cells[0];
  }
  if (type === "array" || type === "uint8-array") return unsignedCells(value, type === "uint8-array" ? 255 : 0xffffffff);
  if (type === "string") return String(value);
  if (type === "string-array") {
    const values = String(value).split(/\r?\n/);
    if (!values.length || values.length > 64 || values.some((item) => !item.length)) throw new Error("Enter one nonempty string per line, up to 64 strings.");
    return values;
  }
  if (type === "phandle" || type === "path") {
    if (!value) throw new Error("Choose an existing local target.");
    return value;
  }
  if (type === "phandles") {
    if (!Array.isArray(value) || !value.length || value.length > 64 || value.some((item) => !item)) throw new Error("Choose one or more existing local targets.");
    return value;
  }
  if (type === "phandle-array") {
    if (!Array.isArray(value) || !value.length || value.length > 64) throw new Error("Add at least one provider reference, up to 64 entries.");
    return value.map((entry) => {
      if (!entry.reference || !Array.isArray(entry.cells)) throw new Error("Choose a provider and complete its specifier cells.");
      return {reference: entry.reference, cells: entry.cells.map((cell) => {
        const parsed = unsignedCells(cell);
        if (parsed.length !== 1) throw new Error("Each provider cell needs one integer.");
        return parsed[0];
      })};
    });
  }
  throw new Error("This property type is not supported by the binding form. Edit it in source.");
}

export function createBindingSession(options) {
  return createDraftObservationSession({...options, route: "bindings", stateKey: "bindingView", labels: {
    changed: "The draft or context changed. Load binding properties for the current source.",
    cancelled: "Binding inspection cancelled. The working draft is unchanged.",
    refreshing: "The overlay draft was updated. Refreshing its binding properties; Save remains a separate action.",
    blocked: "Finish or reconcile the current draft request before loading bindings. Incomplete source stays editable in the source panel.",
    mismatch: "The binding response does not match the current draft. Load binding properties again.",
    updated: "Draft updated. Binding properties now describe this source revision. Save remains a separate action.",
    loaded: "Binding properties loaded for this draft. Defaults and required values have not been added.",
    rejected: "The draft command was not accepted. Reconcile the draft or reload binding properties.",
    pending: "Draft reconciled. Sync retained source before loading binding properties.",
    reconciled: "Draft reconciled. Load binding properties again before applying a change.",
  }});
}
