import {createDraftObservationSession} from "./draft-observation.js";
import {unsignedCells} from "./bindings.js";

export function createPWMSession(options) {
  return createDraftObservationSession({...options, route: "pwm", stateKey: "pwmView",
    validResponse: (data) => ["nodes", "targets", "providers", "consumers", "groups"].every((key) => Array.isArray(data[key]))
      && data.group_option && typeof data.group_option.available === "boolean", labels: {
      changed: "The draft or context changed. Previous PWM details remain visible; load PWM consumers for the current source.",
      cancelled: "PWM inspection cancelled. The working draft is unchanged.",
      refreshing: "The overlay draft was updated. Refreshing PWM consumers; Save remains separate.",
      blocked: "Finish or reconcile the draft request before loading PWM consumers. Repair incomplete source in the source panel.",
      mismatch: "The PWM response does not match the current draft. Load PWM consumers again.",
      updated: "Draft updated. PWM details now describe this revision. Save remains separate.",
      loaded: "Local PWM consumers loaded. No providers, pins or defaults have been added.",
      rejected: "The PWM command was not accepted. Reconcile the draft or reload PWM consumers.",
      pending: "Draft reconciled. Sync retained source before loading PWM consumers.",
      reconciled: "Draft reconciled. Load PWM consumers again before applying a change.",
    }});
}

function integer(value, title) {
  const values = unsignedCells(String(value));
  if (values.length !== 1) throw new Error(`Enter one unsigned integer for ${title}.`);
  return values[0];
}

export function pwmFlags(value, polarity) {
  const flags = integer(value, "flags");
  if (!["0", "1"].includes(String(polarity))) throw new Error("Choose normal or inverted polarity.");
  return flags - flags % 2 + Number(polarity);
}

export function pwmEntry(provider, values) {
  const layout = provider?.cells?.join(",");
  if (provider?.supported === false || !["channel,period", "channel,period,flags"].includes(layout)) {
    throw new Error("Choose a supported local PWM provider with an established cell layout.");
  }
  const channel = integer(values.channel, "channel"); const period = integer(values.period, "period");
  if (!period) throw new Error("PWM period must be greater than zero nanoseconds.");
  const cells = [channel, period];
  if (provider.cells.length === 3) cells.push(integer(values.flags, "flags"));
  return {reference: provider.reference, cells};
}

export function pwmRows(data) {
  return [...data.groups.map((node) => ({key: "group:" + (node.path || node.name), kind: "group", node})),
    ...data.consumers.map((node) => ({key: "consumer:" + (node.path || node.name), kind: "consumer", node}))];
}
