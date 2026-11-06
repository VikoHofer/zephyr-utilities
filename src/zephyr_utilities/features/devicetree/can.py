"""Explicit nominal CAN settings and existing local active transceiver assignments."""

from copy import deepcopy
import re
import time

from .binding_service import BindingService, _literal, _properties
from .can_profiles import CAN_CONTROLLER_PROFILES, CAN_TRANSCEIVER_PROFILES
from .pwm import _compatible_valid, _status


COMMANDS = frozenset({"can_controller_set"})
SCOPE = ("CAN forms cover nominal bitrate, sample point and existing active transceiver references for three reviewed "
         "controller profiles. Missing rates and limits remain unknown; no binding or Kconfig defaults are applied. "
         "Clock timing feasibility, CAN FD configuration, pins, electrical suitability and hardware readiness remain unknown. "
         "Transceiver controls and passive child declarations stay unchanged. Save and Configure remain separate actions.")


def _field(row, name, kind):
    field = deepcopy(next((prop for prop in row["properties"] if prop["name"] == name), None))
    if field is None:
        field = {"name": name, "type": kind, "local": None, "editable": False,
                 "reason": "This property needs a supported field in the matched local binding."}
    if field.get("type") != kind:
        field.update(editable=False, reason="This property has an unsupported binding type for the CAN form.")
    return field


def _value(field):
    return (field.get("local") or {}).get("parsed")


def _valid_value(field, payload):
    if not field.get("editable") or not field.get("local"):
        return None
    value = _value(field)
    try:
        BindingService(None)._typed_value(field, value, payload)
    except ValueError:
        return None
    return value


def _compatibles(document, node):
    value = _literal(document, node, "compatible")
    return ([value.value] if value.kind == "string" else list(value.value)
            if value.kind == "string-array" else []) if value else []


def _profile(document, node, row, profiles):
    compatibles = _compatibles(document, node)
    selected = row["binding"].get("compatible")
    return next((profile for profile in profiles if profile["compatible"] == selected), None) or next(
        (profile for profile in profiles if profile["compatible"] in compatibles), None)


def _identity(document, node, row, profile, payload):
    if profile["compatible"] not in _compatibles(document, node):
        return "A literal local compatible must name this reviewed profile; child-binding metadata alone is insufficient."
    if (row["binding"]["state"] != "matched" or row["binding"].get("compatible") != profile["compatible"]
            or not any(target["id"] == node.id for target in payload["targets"])):
        return "A unique local declaration with the reviewed compatible's matched binding is required."
    if not _compatible_valid(row, payload):
        return "The explicit compatible must satisfy its supported local binding."
    return ""


def _limits(document, node, row, payload):
    maximum_field = _field(row, "max-bitrate", "int")
    maximum = _valid_value(maximum_field, payload)
    minimum = None
    reason = ""
    if not maximum_field.get("required") or type(maximum) is not int or maximum <= 0:
        reason = "The transceiver needs a positive literal max-bitrate satisfying its required integer binding."
    if _properties(node, "min-bitrate"):
        minimum = _valid_value(_field(row, "min-bitrate", "int"), payload)
        if type(minimum) is not int or minimum < 0:
            reason = reason or "The explicit min-bitrate is opaque or invalid; repair it before changing CAN rates or references."
    if type(minimum) is int and type(maximum) is int and minimum > maximum:
        reason = reason or "The explicit minimum bitrate exceeds the maximum bitrate."
    return {"min_bitrate": minimum, "max_bitrate": maximum, "supported": not reason, "reason": reason}


def _rate(document, node, row, payload, names, maximum):
    for name in names:
        if _properties(node, name):
            field = _field(row, name, "int")
            value = _valid_value(field, payload)
            reason = "" if type(value) is int and 1 <= value <= maximum else (
                f"The explicit {name} needs a binding-valid literal rate from 1 to {maximum} before assigning a transceiver.")
            return {"value": value, "property": name, "reason": reason}
    return {"value": None, "property": None, "reason": ""}


def _within_limits(value, limits):
    if value is None:
        return True
    return ((limits["min_bitrate"] is None or value >= limits["min_bitrate"])
            and (limits["max_bitrate"] is None or value <= limits["max_bitrate"]))


def _control_supported(row, name, rows, payload):
    field = _field(row, name, "phandle-array")
    value = _valid_value(field, payload)
    if field.get("specifier_space") != "gpio" or not isinstance(value, list) or len(value) != 1:
        return False
    entry = value[0]
    provider = BindingService._target(field.get("providers", []), entry["reference"], required=False)
    if provider is None or provider.get("cells") != ["pin", "flags"] or len(entry["cells"]) != 2:
        return False
    provider_row = rows[provider["id"]]
    if (_valid_value(_field(provider_row, "#gpio-cells", "int"), payload) != 2
            or _valid_value(_field(provider_row, "gpio-controller", "boolean"), payload) is not True
            or not _compatible_valid(provider_row, payload)):
        return False
    pin, flags = entry["cells"]
    return type(pin) is int and 0 <= pin <= 31 and type(flags) is int and 0 <= flags <= 65535


def can_evidence(document, payload):
    """Share local CAN identities and limits with forms and resource/removal review."""
    rows = {row["node_id"]: row for row in payload["nodes"]}
    targets = {target["id"]: target for target in payload["targets"]}
    transceivers = []
    for row in rows.values():
        node = document.node(row["node_id"])
        profile = _profile(document, node, row, CAN_TRANSCEIVER_PROFILES)
        if profile is None:
            continue
        identity_reason = _identity(document, node, row, profile, payload)
        limits = _limits(document, node, row, payload)
        reason = identity_reason or limits["reason"]
        if _valid_value(_field(row, "#phy-cells", "int"), payload) != 0:
            reason = reason or "An explicit binding-valid #phy-cells = <0> is required for this active CAN transceiver."
        controls = [name for name in ("enable-gpios", "standby-gpios") if _properties(node, name)]
        if not controls:
            reason = reason or "The GPIO transceiver driver requires at least one enable-gpios or standby-gpios control."
        for name in controls:
            if not _control_supported(row, name, rows, payload):
                reason = reason or ("Each existing transceiver control needs one local GPIO specifier with pin/flags cells, "
                                    "binding-valid gpio-controller and #gpio-cells = <2>, pin 0–31 and flags 0–65535.")
        target = targets.get(node.id, {})
        transceivers.append({**deepcopy(target), "id": node.id, "node_id": node.id, "name": row["name"],
                             "path": row["path"], "reference": target.get("reference"), "profile_id": profile["id"],
                             "compatible": profile["compatible"], "status": _status(document, node),
                             "identity_supported": not identity_reason, **limits, "supported": not reason, "reason": reason})

    supported = [phy for phy in transceivers if phy["supported"]]
    controllers = []
    for row in rows.values():
        node = document.node(row["node_id"])
        profile = _profile(document, node, row, CAN_CONTROLLER_PROFILES)
        if profile is None:
            continue
        identity_reason = _identity(document, node, row, profile, payload)
        expected = {"bitrate": "int", "sample-point": "int", "phys": "phandle"}
        bound = {field["name"]: field for field in row["properties"]}
        if any(bound.get(name, {}).get("type") != kind for name, kind in expected.items()):
            identity_reason = identity_reason or "The reviewed CAN profile needs common integer bitrate/sample-point and phandle phys binding fields."
        fields = {name: _field(row, name, kind) for name, kind in (("bitrate", "int"), ("sample-point", "int"), ("phys", "phandle"))}
        passive_nodes = [child for child in document.syntax.nodes if child.parent_id == node.id
                         and re.sub(r"[-,.@/+]", "_", child.name.lower()) == "can_transceiver"]
        passive = None
        if passive_nodes:
            child = passive_nodes[0]
            passive = {"node_id": child.id, "path": rows[child.id]["path"], **_limits(document, child, rows[child.id], payload)}
            if (len(passive_nodes) != 1 or child.name != "can-transceiver" or child.id not in targets
                    or rows[child.id]["binding"]["state"] != "matched"):
                passive.update(supported=False, reason="The passive can-transceiver child needs one uniquely named local matched declaration; ambiguous normalized names require source editing.")
        phys = _value(fields["phys"])
        limits = {"min_bitrate": None, "max_bitrate": None, "source": "unknown", "node_id": None, "reason": ""}
        notes = []
        if _properties(node, "phys"):
            active = BindingService._target(transceivers, phys, required=False)
            limits.update(source="phys", node_id=active["id"] if active else None)
            if active:
                limits.update({key: active[key] for key in ("min_bitrate", "max_bitrate", "reason")})
            if active is None or not active["supported"] or not fields["phys"]["editable"]:
                limits["reason"] = limits["reason"] or "The current active transceiver is unresolved or outside the supported local profile."
            if passive:
                notes.append("The active phys reference takes precedence over the preserved passive can-transceiver child.")
        elif passive:
            limits.update({key: passive[key] for key in ("min_bitrate", "max_bitrate", "reason", "node_id")})
            limits["source"] = "passive"
            notes.append("Assigning an active transceiver makes phys take precedence over the preserved passive can-transceiver child.")
        nominal = _rate(document, node, row, payload, ("bitrate", "bus-speed"), 1000000)
        data_rate = _rate(document, node, row, payload, ("bitrate-data", "bus-speed-data"), 8000000)
        for field in fields.values():
            if identity_reason:
                field.update(editable=False, reason=identity_reason)
        if not _properties(node, "bitrate") and _properties(node, "bus-speed"):
            fields["bitrate"].update(editable=False, reason="The deprecated bus-speed is preserved. Migrate it explicitly in source before setting modern bitrate.")
        if limits["reason"]:
            for name in ("bitrate", "phys"):
                fields[name].update(editable=False, reason=limits["reason"])
        fields["phys"]["providers"] = deepcopy(supported)
        if not supported:
            fields["phys"].update(editable=False, reason="No supported local active CAN transceiver is available.")
        if nominal["reason"] or data_rate["reason"]:
            fields["phys"].update(editable=False, reason=nominal["reason"] or data_rate["reason"])
        if nominal["value"] is None:
            notes.append("No supported local nominal bitrate is established; inherited and Kconfig rates remain unknown.")
        if profile["fd"] or data_rate["property"]:
            notes.append("CAN FD settings are preserved. Only explicit literal data rates are checked against transceiver bounds; effective FD configuration remains unknown.")
        editable = any(field["editable"] for field in fields.values())
        controllers.append({"node_id": node.id, "name": row["name"], "path": row["path"], "profile_id": profile["id"],
                            "compatible": profile["compatible"], "status": _status(document, node),
                            "identity_supported": not identity_reason, "editable": editable,
                            "reason": "" if editable else identity_reason or next(field["reason"] for field in fields.values()),
                            "fields": fields, "bitrate": _value(fields["bitrate"]), "sample_point": _value(fields["sample-point"]),
                            "phys": phys, "passive_transceiver": passive, "limits": limits, "nominal_rate": nominal,
                            "data_rate": data_rate, "notes": notes})
    return {"controllers": controllers, "transceivers": transceivers}


class CANService(BindingService):
    """Apply selected properties as one source-preserving canonical draft action."""

    commands = COMMANDS

    @staticmethod
    def _projection(document, catalog):
        payload = BindingService._projection(document, catalog)
        payload.update(can_evidence(document, payload))
        payload["diagnostics"].insert(0, SCOPE)
        return payload

    def plan(self, body, context, draft):
        command = body["command"]
        if (set(command) != {"kind", "schema_id", "node_id", "values"} or command["kind"] != "can_controller_set"
                or not isinstance(command["schema_id"], str) or not isinstance(command["values"], dict)
                or not command["values"] or set(command["values"]) - {"bitrate", "sample-point", "phys"}):
            raise ValueError("Choose a nonempty subset of the supported CAN controller properties.")
        observed = self.observations.get(command["schema_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("CAN forms belong to an earlier draft/context or expired. Load CAN controllers again.")
        observed["catalog"].revalidate()
        payload = observed["payload"]
        controller = next((item for item in payload["controllers"] if item["node_id"] == command["node_id"]), None)
        if controller is None or not controller["editable"]:
            raise ValueError(controller["reason"] if controller else "Choose a supported local CAN controller.")
        values = command["values"]
        for name, value in values.items():
            field = controller["fields"][name]
            if not field["editable"]:
                raise ValueError(field["reason"])
            if name == "bitrate" and (type(value) is not int or not 1 <= value <= 1000000):
                raise ValueError("Enter a nominal CAN bitrate from 1 to 1000000 bits per second; clock timing feasibility remains unknown.")
            if name == "sample-point" and (type(value) is not int or not 0 <= value <= 999):
                raise ValueError("Enter a CAN sample point from 0 to 999 per mille; 0 requests automatic selection.")
        limits = controller["limits"]
        if "phys" in values:
            limits = self._target(controller["fields"]["phys"]["providers"], values["phys"])
        if "bitrate" in values or "phys" in values:
            nominal = values.get("bitrate", controller["nominal_rate"]["value"])
            if not _within_limits(nominal, limits):
                raise ValueError("The nominal bitrate lies outside the effective local transceiver's explicit limits.")
            if "phys" in values and not _within_limits(controller["data_rate"]["value"], limits):
                raise ValueError("The preserved CAN FD data rate lies outside the selected transceiver's explicit limits.")
        properties = [(name, self._typed_value(controller["fields"][name], value, payload)) for name, value in values.items()]
        change = draft.document.plan_update_node(controller["node_id"], properties=properties)
        observed["catalog"].revalidate()
        return change
