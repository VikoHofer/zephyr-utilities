import {createDraftObservationSession} from "./draft-observation.js";
import {unsignedCells} from "./bindings.js";

export function createADCSession(options) {
  return createDraftObservationSession({...options, route: "adc", stateKey: "adcView",
    validResponse: (data) => ["nodes", "targets", "providers", "consumers", "channels"].every((key) => Array.isArray(data[key])), labels: {
      changed: "The draft or context changed. Previous ADC details remain visible; load ADC channels for the current source.",
      cancelled: "ADC inspection cancelled. The working draft is unchanged.",
      refreshing: "The overlay draft was updated. Refreshing ADC channels; Save remains separate.",
      blocked: "Finish or reconcile the draft request before loading ADC channels. Repair incomplete source in the source panel.",
      mismatch: "The ADC response does not match the current draft. Load ADC channels again.",
      updated: "Draft updated. ADC details now describe this revision. Save remains separate.",
      loaded: "Local ADC channels loaded. No providers, pins or defaults have been added.",
      rejected: "The ADC command was not accepted. Reconcile the draft or reload ADC channels.",
      pending: "Draft reconciled. Sync retained source before loading ADC channels.",
      reconciled: "Draft reconciled. Load ADC channels again before applying a change.",
    }});
}

function integer(value, title, maximum) {
  const values = unsignedCells(String(value));
  if (values.length !== 1 || values[0] > maximum) throw new Error(`Enter one unsigned integer from 0 to ${maximum} for ${title}.`);
  return values[0];
}

export function adcChannel(value) { return integer(value, "ADC channel", 31); }

export function adcEntry(provider, channel) {
  if (provider?.supported !== true || provider?.cells?.join(",") !== "input") {
    throw new Error("Choose a supported local ADC provider with the input cell layout.");
  }
  return {reference: provider.reference, cells: [adcChannel(channel)]};
}

export function adcConfig(fields, values) {
  for (const name of ["gain", "reference"]) {
    if (!Array.isArray(fields?.[name]?.enum) || !fields[name].enum.includes(values[name])) {
      throw new Error(`Choose an explicit ${name} from the local ADC binding.`);
    }
  }
  return {gain: values.gain, reference: values.reference,
    acquisition_time: integer(values.acquisition_time, "encoded acquisition time", 65535)};
}

export function adcRows(data) {
  return [...data.providers.map((node) => ({key: "provider:" + (node.path || node.reference), kind: "provider", node})),
    ...data.channels.map((node) => ({key: "channel:" + (node.path || node.name), kind: "channel", node})),
    ...data.consumers.map((node) => ({key: "consumer:" + (node.path || node.name), kind: "consumer", node}))];
}
