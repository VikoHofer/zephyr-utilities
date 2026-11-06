"""Bounded Nordic pin expressions and lossless overlay-only pin operations.

Inherited input is supplied by the selected-checkout evidence adapter. This module
does not load files, preprocess source or infer an effective tree from arbitrary DTS.
"""

from copy import deepcopy
import re

from .source import PropertyValue, SourceDocument, SourceError


DEFAULT_FLAGS = ("GPIO_ACTIVE_HIGH", "GPIO_ACTIVE_LOW", "GPIO_PULL_UP", "GPIO_PULL_DOWN", "GPIO_OPEN_DRAIN")
_NUMBER = re.compile(rb"(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)")
_ROLE = re.compile(rb"[A-Z][A-Z_0-9]{0,63}")
_LABEL = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,127}")
_TRIVIA = {"whitespace", "comment", "bom"}
_PIN_HEADER = b"#include <zephyr/dt-bindings/pinctrl/nrf-pinctrl.h>"
_GPIO_HEADER = b"#include <zephyr/dt-bindings/gpio/gpio.h>"


def _tokens(document, start, end):
    return [(token, document.source[token.start:token.end])
            for token in document._tokens(start, end) if token.kind not in _TRIVIA]


def _integer(raw):
    if len(raw) > 10 or not _NUMBER.fullmatch(raw):
        raise SourceError("Pin expressions require literal unsigned integers.")
    return int(raw, 16 if raw.lower().startswith(b"0x") else 10)


def _pin(value):
    if (not isinstance(value, dict) or set(value) != {"port", "pin"}
            or type(value["port"]) is not int or value["port"] not in (0, 1)
            or type(value["pin"]) is not int or not 0 <= value["pin"] < (32 if value["port"] == 0 else 16)):
        raise SourceError("Choose an nRF52840 pin from P0.00–P0.31 or P1.00–P1.15.")
    return value


def _unique_property(node, name):
    found = [prop for prop in node.properties if prop.name == name]
    if len(found) > 1:
        raise SourceError("Repeated property declarations need explicit source editing.")
    return found[0] if found else None


def _refs(document, prop):
    if prop.kind == "delete-property":
        return []
    if prop.value_start is None:
        raise SourceError("Pin state references need a literal phandle list.")
    tokens = _tokens(document, prop.value_start, prop.value_end)
    if (len(tokens) < 2 or tokens[0][1] != b"<" or tokens[-1][1] != b">"
            or any(token.kind != "reference" or value.startswith(b"&{") for token, value in tokens[1:-1])):
        raise SourceError("Pin state references need one literal list of state labels.")
    labels = [value[1:].decode("ascii") for _, value in tokens[1:-1]]
    if len(labels) > 16 or len(labels) != len(set(labels)):
        raise SourceError("Repeated or excessive state references need source editing.")
    return labels


def _psels(document, prop):
    if prop.kind != "property" or prop.value_start is None:
        raise SourceError("Pin groups need an explicit psels property.")
    tokens = _tokens(document, prop.value_start, prop.value_end)
    result, index, inside, separator, cell_count = [], 0, False, False, 0
    while index < len(tokens):
        token, raw = tokens[index]
        if raw == b"<" and not inside and not separator:
            inside, cell_count = True, 0
            index += 1
            continue
        if raw == b">" and inside and cell_count:
            inside, separator = False, True
            index += 1
            continue
        if raw == b"," and separator:
            separator = False
            index += 1
            continue
        part = tokens[index:index + 8]
        if (not inside or len(part) != 8 or [value for _, value in part[0:2]] != [b"NRF_PSEL", b"("]
                or not _ROLE.fullmatch(part[2][1]) or part[3][1] != b"," or part[5][1] != b"," or part[7][1] != b")"):
            raise SourceError("Only explicit NRF_PSEL(signal, port, pin) entries are editable; other expressions remain in source.")
        port, pin = _integer(part[4][1]), _integer(part[6][1])
        _pin({"port": port, "pin": pin})
        result.append({"role": part[2][1].decode("ascii"), "port": port, "pin": pin,
                       "start": token.start, "end": part[-1][0].end,
                       "port_token": part[4][0], "pin_token": part[6][0]})
        index += 8
        cell_count += 1
    if inside or not separator or not result or len(result) > 64:
        raise SourceError("Pin groups need a bounded nonempty list of NRF_PSEL expressions.")
    return result


def _comments(document, start, end):
    return [token for token in document._tokens(start, end) if token.kind == "comment"]


def _expression(role, pin):
    return f"NRF_PSEL({role}, {pin['port']}, {pin['pin']})".encode("ascii")


def _replace_property(document, node, name, rendered):
    """Replace a validated bounded expression; leave trivia outside its value intact."""
    prop = _unique_property(node, name)
    if not prop:
        return document._insert(node, name.encode("ascii") + b" = " + rendered + b";", property=True)
    if prop.kind == "delete-property":
        return document._edit(prop.start, prop.end, name.encode("ascii") + b" = " + rendered + b";")
    if prop.value_start is None:
        raise SourceError("A boolean property cannot be used as a pin reference.")
    if _comments(document, prop.value_start, prop.value_end):
        raise SourceError("Changing this expression's shape would remove comments; edit its source explicitly.")
    return document._edit(prop.value_start, prop.value_end, rendered)


def _apply(document, edits):
    return document._plan("nordic-source-step", edits).apply(document)


def _finish(original, document, operation):
    if original.source == document.source:
        return original._plan(operation, [])
    # All intermediate edits address checked lexical spans. One final canonical
    # command revision owns the complete byte result and its original preconditions.
    return original._plan(operation, [original._edit(0, len(original.source), document.source)])


def _header(document, header):
    if any(token.kind == "directive" and document.source[token.start:token.end].strip() == header
           for token in document.syntax.tokens):
        return document
    position = 3 if document.source.startswith(b"\xef\xbb\xbf") else 0
    return _apply(document, [document._edit(position, position, header + document._newline(position))])


def _copied_statement(raw, newline):
    """Adapt a copied block's outer indentation without reformatting its contents."""
    lines = raw.replace(b"\r\n", b"\n").split(b"\n")
    closing = re.fullmatch(rb"([ \t]+)};", lines[-1])
    if closing:
        base = closing[1]
        lines = [lines[0], *(line[len(base):] if line.startswith(base) else line for line in lines[1:])]
    return newline.join(lines)


class NordicSource:
    """Project known board pin states and uniquely described local overrides."""

    def __init__(self, document, inherited_states, controllers, *, gpio_flags=None):
        if not isinstance(document, SourceDocument):
            raise SourceError("Choose a canonical source document.")
        self.document = document
        self.inherited_states = inherited_states
        self.controllers = controllers
        self.gpio_flags = tuple(DEFAULT_FLAGS if gpio_flags is None else gpio_flags)
        self._states = {}
        self._views = None
        self._identities = None

    def _local_identities(self):
        """Identify literal local label/path contributors without merging their data."""
        if self._identities is not None:
            return self._identities
        document = self.document
        labels, identities = {}, {}
        aliases = {alias: item["id"] for item in self.controllers for alias in item.get("aliases", [])}
        for node in document.syntax.nodes:
            for label in node.labels:
                labels.setdefault(label, []).append(node)

        def resolve(node, visiting):
            if node.id in identities:
                return identities[node.id]
            if node.id in visiting or len(visiting) >= 128:
                return None
            visiting = visiting | {node.id}
            if node.name.startswith("&{"):
                identity = node.name[2:-1]
            elif node.name.startswith("&"):
                label = node.name[1:]
                targets = labels.get(label, [])
                identity = (resolve(targets[0], visiting) if len(targets) == 1 else None if targets
                            else "&" + aliases.get(label, label))
            elif node.parent_id:
                parent = resolve(document.node(node.parent_id), visiting)
                identity = parent.rstrip("/") + "/" + node.name if parent is not None else None
            else:
                identity = "/" if node.name == "/" else None
            identities[node.id] = identity
            return identity

        for node in document.syntax.nodes:
            resolve(node, set())
        contributors = {}
        for node_id, identity in identities.items():
            if identity is not None:
                contributors.setdefault(identity, []).append(node_id)
        self._identities = identities, contributors, labels
        return self._identities

    def _unique_local_node(self, node, *, ancestors=False):
        identities, contributors, labels = self._local_identities()
        while node:
            identity = identities[node.id]
            if identity is None or len(contributors[identity]) != 1 or any(len(labels[label]) != 1 for label in node.labels):
                raise SourceError("This node or an ancestor has multiple or ambiguous local contributors; edit their source explicitly.")
            node = self.document.node(node.parent_id) if ancestors and node.parent_id else None

    def _controller_node(self, descriptor):
        labels = {descriptor["id"], *descriptor.get("aliases", [])}
        nodes = [node for node in self.document.syntax.nodes
                 if node.name in {"&" + label for label in labels} or labels.intersection(node.labels)]
        if len(nodes) > 1:
            raise SourceError("This controller has multiple local contributors; edit their source explicitly.")
        node = nodes[0] if nodes else None
        if node:
            if not node.name.startswith("&") or node.parent_id is not None:
                raise SourceError("The known controller label is shadowed by a local declaration.")
            self.document._node_editable(node)
        return node

    def _state(self, label):
        if label in self._states:
            return self._states[label]
        if not _LABEL.fullmatch(label):
            raise SourceError("Pin states need literal node labels.")
        document = self.document
        nodes = [node for node in document.syntax.nodes if label in node.labels]
        extensions = [node for node in document.syntax.nodes if node.name == "&" + label]
        if len(nodes) > 1 or extensions or nodes and label in self.inherited_states:
            raise SourceError("This pin state has repeated or inherited contributors; edit its source explicitly.")
        local = bool(nodes)
        if local:
            node = nodes[0]
            parent = document.node(node.parent_id) if node.parent_id else None
            if parent is None or parent.name != "&pinctrl" or parent.parent_id is not None:
                raise SourceError("Editable pin states must be direct children of an explicit &pinctrl block.")
            document._node_editable(node)
            self._unique_local_node(node)
        elif label in self.inherited_states:
            raw = self.inherited_states[label]
            document = SourceDocument.parse(raw if raw.lstrip().startswith(b"&pinctrl") else b"&pinctrl {\n" + raw + b"\n};")
            nodes = [node for node in document.syntax.nodes if label in node.labels]
            # Evidence may provide the complete child alone or an &pinctrl wrapper.
            if len(nodes) != 1:
                raise SourceError("Inherited pin state evidence is incomplete or ambiguous.")
            node = nodes[0]
        else:
            raise SourceError("This pin state is outside the supported board and local source evidence.")
        if not document.can_edit_node(node.id):
            raise SourceError("This pin state contains unsafe or incomplete source structure.")
        if len(node.labels) != 1:
            raise SourceError("A state with multiple label aliases needs explicit source editing.")
        groups = []
        for child_id in node.children:
            group = document.node(child_id)
            if local:
                self._unique_local_node(group)
            if group.children or any(item.parent_id == group.id for item in document.syntax.deletions):
                raise SourceError("Nested or deleted pin groups need explicit source editing.")
            group_refs = {"&" + label for label in group.labels}
            if any(token.kind == "reference" and document.source[token.start:token.end].decode("ascii") in group_refs
                   and not group.start <= token.start < group.end for token in document.syntax.tokens):
                raise SourceError("A pin group has a direct local consumer; edit or split those dependencies explicitly in source.")
            prop = _unique_property(group, "psels")
            if prop is None:
                raise SourceError("A pin group without explicit psels remains unknown.")
            groups.append({"node": group, "property": prop, "signals": _psels(document, prop)})
        if any(item.parent_id == node.id for item in document.syntax.deletions):
            raise SourceError("Deleted pin groups need explicit source editing.")
        result = {"label": label, "document": document, "node": node, "groups": groups, "local": local}
        self._states[label] = result
        return result

    def _view(self, descriptor):
        view = {key: deepcopy(descriptor.get(key)) for key in ("id", "kind", "instance", "compatible", "path")}
        view.update(label=descriptor.get("label", descriptor["id"]), status=descriptor.get("status", "unknown"),
                    editable=descriptor.get("editable", True), reason=descriptor.get("reason", ""), states=[])
        node, names, reason = None, list(descriptor.get("states", {})), ""
        try:
            self.document._editable()
            node = self._controller_node(descriptor)
            if node:
                prop = _unique_property(node, "status")
                if prop:
                    value = self.document.property_value(prop.id)
                    view["status"] = value.value if value and value.kind == "string" else "unknown"
                prop = _unique_property(node, "pinctrl-names")
                if prop:
                    value = self.document.property_value(prop.id)
                    if value is None or value.kind not in {"string", "string-array"}:
                        raise SourceError("Pin state names must be unique literal strings.")
                    names = [value.value] if value.kind == "string" else list(value.value)
            if len(names) != len(set(names)) or any(name not in {"default", "sleep"} for name in names):
                raise SourceError("Only unambiguous default and sleep state names are supported.")
            if node:
                for prop in node.properties:
                    if match := re.fullmatch(r"pinctrl-(\d+)", prop.name):
                        if int(match[1]) >= len(names) or str(int(match[1])) != match[1]:
                            raise SourceError("A pinctrl property has no matching state name; resolve its meaning in source first.")
            if view["status"] not in {"okay", "ok", "disabled"}:
                raise SourceError("This controller's enabled status is unknown; resolve its source first.")
        except SourceError as exc:
            reason = str(exc)
            view.update(editable=False, reason=reason)
        inherited = list(descriptor.get("states", {}).values())
        for name in ("default", "sleep"):
            state = {"name": name, "editable": view["editable"], "reason": reason or view["reason"],
                     "shared": False, "consumers": [], "signals": [], "roles": list(descriptor.get("roles", [])),
                     "refs": [], "index": names.index(name) if name in names else len(names), "property": None, "known": False}
            try:
                if reason:
                    raise SourceError(reason)
                if name not in names:
                    if node and _unique_property(node, "pinctrl-" + str(len(names))):
                        raise SourceError("A pinctrl property has no state name; resolve its meaning in source first.")
                    state["known"] = True
                    view["states"].append(state)
                    continue
                index = names.index(name)
                prop = _unique_property(node, f"pinctrl-{index}") if node else None
                refs = _refs(self.document, prop) if prop else list(inherited[index] if index < len(inherited) else [])
                state.update(refs=refs, property=prop)
                for label in refs:
                    resolved = self._state(label)
                    for group in resolved["groups"]:
                        for item in group["signals"]:
                            state["signals"].append({"role": item["role"], "port": item["port"], "pin": item["pin"],
                                "editable": view["editable"] and item["role"] in state["roles"], "reason": "",
                                "label": label, "group": group["node"].name, "origin": "overlay" if resolved["local"] else "board"})
                roles = [item["role"] for item in state["signals"]]
                if len(roles) != len(set(roles)):
                    raise SourceError("A signal appears more than once in this state; resolve it in source first.")
                if any(role not in state["roles"] for role in roles) and view["editable"]:
                    raise SourceError("This state contains signal roles outside this controller's supported profile.")
                state["known"] = True
            except SourceError as exc:
                state.update(editable=False, reason=str(exc))
            view["states"].append(state)
        view["_node"], view["_names"] = node, names
        return view

    def _project(self):
        if self._views is not None:
            return self._views
        views = [self._view(item) for item in self.controllers]
        consumers, spans = {}, []
        for view in views:
            for state in view["states"]:
                for label in state["refs"]:
                    consumers.setdefault(label, set()).add(view["id"] + "/" + state["name"])
                prop = state["property"]
                if prop:
                    spans.append((prop.start, prop.end))
        for token in self.document.syntax.tokens:
            if token.kind != "reference" or any(start <= token.start < end for start, end in spans):
                continue
            label = self.document.source[token.start:token.end].decode("ascii")[1:]
            if label in self._states:
                consumers.setdefault(label, set()).add("Other local source")
        for view in views:
            for state in view["states"]:
                own = view["id"] + "/" + state["name"]
                others = set().union(*(consumers.get(label, set()) - {own} for label in state["refs"]))
                state.update(shared=bool(others), consumers=sorted(others))
        self._views = views
        return views

    def project(self):
        views = []
        for view in self._project():
            public = {key: deepcopy(value) for key, value in view.items() if not key.startswith("_") and key != "states"}
            public["states"] = [{key: deepcopy(value) for key, value in state.items() if key != "property"}
                                for state in view["states"]]
            views.append(public)
        return {"controllers": views, "gpio": self.gpio(), "diagnostics": []}

    def _new_label(self, controller, state):
        labels = set(self.inherited_states)
        labels.update(self.document.source[token.start:token.end].decode("ascii") for token in self.document._label_tokens())
        names = {node.name for node in self.document.syntax.nodes}
        base = "zephyr_utils_" + controller + "_" + state
        label, index = base, 1
        while label in labels or label in names:
            label, index = base + "_" + str(index), index + 1
        return label

    @staticmethod
    def _modified_state(resolved, signal, pin, state_name):
        """Return changed bytes for one state, or None when its final group is removed."""
        document, node = resolved["document"], resolved["node"]
        found = [(group, item) for group in resolved["groups"] for item in group["signals"] if item["role"] == signal]
        if len(found) > 1:
            raise SourceError("A signal appears repeatedly in the selected state.")
        if not found and pin is None:
            return document.source[node.start:node.end]
        edits = []
        if found:
            group, item = found[0]
            if pin is not None:
                for key in ("port", "pin"):
                    token = item[key + "_token"]
                    if item[key] != pin[key]:
                        old = document.source[token.start:token.end]
                        raw = (old[:2] + format(pin[key], "X" if old.startswith(b"0X") else "x").encode("ascii")
                               if old.lower().startswith(b"0x") else str(pin[key]).encode("ascii"))
                        edits.append(document._edit(token.start, token.end, raw))
            elif len(group["signals"]) == 1:
                removed = group["node"]
                if _comments(document, removed.start, removed.end):
                    raise SourceError("Removing the empty group would discard comments; edit its source explicitly.")
                safe_settings = {"psels", "bias-pull-up", "bias-pull-down", "bias-disable", "low-power-enable", "nordic,invert"}
                if any(prop.name not in safe_settings or prop.name != "psels" and
                       (prop.kind != "property" or prop.value_start is not None) for prop in removed.properties):
                    raise SourceError("The empty group has other settings that must be preserved; edit its source explicitly.")
                group_labels = {"&" + label for label in removed.labels}
                if any(token.kind == "reference" and document.source[token.start:token.end].decode("ascii") in group_labels
                       and not removed.start <= token.start < removed.end for token in document.syntax.tokens):
                    raise SourceError("The empty group has another local consumer; preserve it or split its source explicitly.")
                if len(resolved["groups"]) == 1 and not node.properties:
                    if _comments(document, node.start, node.end):
                        raise SourceError("Removing the empty state would discard comments; edit its source explicitly.")
                    return None
                edits.append(document._edit(removed.start, removed.end, b""))
            else:
                prop = group["property"]
                if _comments(document, prop.value_start, prop.value_end):
                    raise SourceError("Removing this signal would discard inline comments; edit its expression in source.")
                values = ["<" + _expression(other["role"], other).decode("ascii") + ">"
                          for other in group["signals"] if other is not item]
                edits.append(document._edit(prop.value_start, prop.value_end, ", ".join(values).encode("ascii")))
        else:
            names = {group["node"].name for group in resolved["groups"]}
            name, index = "zephyr_utils_group", 1
            while name in names:
                name, index = "zephyr_utils_group_" + str(index), index + 1
            _, unit, newline = document._layout(node)
            statement = name.encode("ascii") + b" {" + newline + unit + b"psels = <" + _expression(signal, pin) + b">;" + newline
            if state_name == "sleep":
                statement += unit + b"low-power-enable;" + newline
            statement += b"};"
            edits.append(document._insert(node, statement))
        candidate = _apply(document, edits)
        updated = next(item for item in candidate.syntax.nodes if resolved["label"] in item.labels)
        return candidate.source[updated.start:updated.end]

    def plan(self, controller, state, signal, pin, *, split=False):
        self.document._editable()
        if type(split) is not bool or state not in {"default", "sleep"}:
            raise SourceError("Choose a supported pin state and explicit split option.")
        views = [view for view in self._project() if view["id"] == controller]
        if len(views) != 1:
            raise SourceError("Choose a known controller from the current pin observation.")
        view = views[0]
        selected = next(item for item in view["states"] if item["name"] == state)
        if not selected["editable"]:
            raise SourceError(selected["reason"] or "This pin state is read-only.")
        if signal not in selected["roles"]:
            raise SourceError("Choose a signal role supported by this controller.")
        if pin is not None:
            _pin(pin)
        existing = next((item for item in selected["signals"] if item["role"] == signal), None)
        if existing and pin is not None and (existing["port"], existing["pin"]) == (pin["port"], pin["pin"]):
            return self.document._plan("nordic-pin-assign", [])
        if existing is None and pin is None:
            return self.document._plan("nordic-pin-unassign", [])
        if selected["shared"] and not split:
            raise SourceError("This state has other consumers. Explicitly split it before changing this controller.")
        refs = list(selected["refs"])
        label = existing["label"] if existing else refs[0] if refs else None
        resolved = self._state(label) if label else None
        changed = self._modified_state(resolved, signal, pin, state) if resolved else None
        copy_state = resolved is None or not resolved["local"] or selected["shared"]
        document = self.document
        edits = []
        if resolved is None:
            label = self._new_label(controller, state)
            newline = document._newline(len(document.source))
            changed = (label + ": " + label + " {").encode("ascii") + newline + b"\tgroup1 {" + newline
            changed += b"\t\tpsels = <" + _expression(signal, pin) + b">;" + newline
            if state == "sleep":
                changed += b"\t\tlow-power-enable;" + newline
            changed += b"\t};" + newline + b"};"
            refs.append(label)
        elif copy_state and changed is not None:
            state_doc = SourceDocument.parse(b"&pinctrl {" + changed + b"};")
            old = next(item for item in state_doc.syntax.nodes if label in item.labels)
            nested_labels = [token for token in state_doc._label_tokens() if old.open_brace < token.start < old.end]
            if nested_labels:
                raise SourceError("Splitting a state with descendant labels needs explicit source editing to preserve its consumers.")
            new_label = self._new_label(controller, state)
            header = _tokens(state_doc, old.start, old.open_brace)
            if len(header) != 3 or header[1][1] != b":":
                raise SourceError("This state's declaration header needs explicit source editing before splitting.")
            renamed = _apply(state_doc, [state_doc._edit(token.start, token.end, new_label.encode("ascii"))
                                        for token, _ in (header[0], header[2])])
            copied = next(item for item in renamed.syntax.nodes if new_label in item.labels)
            changed = renamed.source[copied.start:copied.end]
            refs[refs.index(label)] = new_label
        elif changed is None:
            refs.remove(label)
        if resolved and resolved["local"] and not copy_state:
            node = resolved["node"]
            edits.append(document._edit(node.start, node.end, changed or b""))
        elif changed is not None:
            changed = _copied_statement(changed, document._newline(len(document.source)))
            blocks = [node for node in document.syntax.nodes if node.name == "&pinctrl" and node.parent_id is None]
            if any(not document.can_edit_node(node.id) for node in blocks):
                raise SourceError("The local pinctrl block has unresolved deletion or structural conflicts.")
            if blocks:
                edits.append(document._insert(blocks[-1], changed))
            else:
                newline = document._newline(len(document.source))
                statement = b"&pinctrl {" + newline + b"\t" + changed.replace(newline, newline + b"\t") + newline + b"};"
                edits.append(document._insert(None, statement))
        document = _apply(document, edits)
        # Re-resolve nodes after byte changes because source identities are revision-bound.
        descriptor = next(item for item in self.controllers if item["id"] == controller)
        fresh = NordicSource(document, self.inherited_states, self.controllers, gpio_flags=self.gpio_flags)
        node = fresh._controller_node(descriptor)
        rendered = b"<" + b" ".join(("&" + item).encode("ascii") for item in refs) + b">"
        propname = "pinctrl-" + str(selected["index"])
        add_name = state not in view["_names"]
        if node:
            # When reference count/shape is unchanged, update only label leaves so
            # comments between state references survive a private-state split.
            prop = _unique_property(node, propname)
            oldrefs = _refs(document, prop) if prop else []
            if prop and prop.kind == "property" and len(oldrefs) == len(refs):
                leaves = [token for token, _ in _tokens(document, prop.value_start, prop.value_end) if token.kind == "reference"]
                edits = [document._edit(token.start, token.end, ("&" + new).encode("ascii"))
                         for token, oldref, new in zip(leaves, oldrefs, refs) if oldref != new]
            else:
                edits = [_replace_property(document, node, propname, rendered)]
            document = _apply(document, edits)
            if add_name:
                fresh = NordicSource(document, self.inherited_states, self.controllers, gpio_flags=self.gpio_flags)
                node = fresh._controller_node(descriptor)
                names = PropertyValue.strings([*view["_names"], state]).render()
                document = _apply(document, [_replace_property(document, node, "pinctrl-names", names)])
        else:
            newline = document._newline(len(document.source))
            statement = ("&" + controller + " {").encode("ascii") + newline + b"\t" + propname.encode("ascii") + b" = " + rendered + b";" + newline
            if add_name:
                statement += b"\tpinctrl-names = " + PropertyValue.strings([*view["_names"], state]).render() + b";" + newline
            statement += b"};"
            document = _apply(document, [document._insert(None, statement)])
        if pin is not None:
            document = _header(document, _PIN_HEADER)
        return _finish(self.document, document, "nordic-pin-unassign" if pin is None else "nordic-pin-assign")

    def _gpio_entries(self, prop):
        document = self.document
        if prop.kind == "delete-property":
            return []
        if prop.value_start is None:
            raise SourceError("GPIO assignments need explicit provider, pin and flags cells.")
        tokens = _tokens(document, prop.value_start, prop.value_end)
        entries, position = [], 0
        while position < len(tokens):
            start = position
            if position and tokens[position][1] == b",":
                position += 1
            if position + 5 > len(tokens) or tokens[position][1] != b"<" or tokens[position + 1][1] not in {b"&gpio0", b"&gpio1"}:
                raise SourceError("GPIO forms support explicit gpio0/gpio1 entries; mapped or opaque providers stay in source.")
            port = int(tokens[position + 1][1][-1:])
            pin = _integer(tokens[position + 2][1])
            _pin({"port": port, "pin": pin})
            first = position
            position += 3
            flags_start = position
            while position < len(tokens) and tokens[position][1] != b">":
                position += 1
            if position == len(tokens):
                raise SourceError("The GPIO entry is incomplete.")
            flag_tokens = tokens[flags_start:position]
            raw = b"".join(value for _, value in flag_tokens)
            if raw.startswith(b"(") and raw.endswith(b")"):
                raw = raw[1:-1]
            parts = raw.split(b"|")
            if len(parts) == 1 and _NUMBER.fullmatch(parts[0]):
                # Zero has an exact symbolic equivalent. Other numeric bitsets are
                # preserved in source because this adapter does not decode them.
                flags = ["GPIO_ACTIVE_HIGH"] if _integer(parts[0]) == 0 else None
            else:
                flags = [value.decode("ascii", errors="replace") for value in parts]
                if any(flag not in self.gpio_flags for flag in flags):
                    flags = None
            if not flags or len(flags) != len(set(flags)):
                raise SourceError("Use known symbolic GPIO flags; unknown numeric bits and expressions remain in source.")
            entries.append({"port": port, "pin": pin, "flags": flags, "start": tokens[first][0].start,
                            "end": tokens[position][0].end, "provider_token": tokens[first + 1][0],
                            "pin_token": tokens[first + 2][0], "flags_start": flag_tokens[0][0].start,
                            "flags_end": flag_tokens[-1][0].end, "separator_start": tokens[start][0].start})
            position += 1
            if position < len(tokens) and tokens[position][1] != b",":
                raise SourceError("GPIO entries require separate explicit cell groups.")
        if not entries or len(entries) > 32:
            raise SourceError("Choose a bounded nonempty GPIO list.")
        return entries

    def gpio(self):
        rows = []
        document = self.document
        for node in document.syntax.nodes:
            for prop in node.properties:
                if prop.name != "gpios" and not prop.name.endswith("-gpios"):
                    continue
                base = {"node_id": node.id, "name": node.name, "path": document._path(node), "property": prop.name,
                        "editable": True, "reason": "", "deleted": prop.kind == "delete-property"}
                try:
                    document._editable()
                    document._node_editable(node)
                    _unique_property(node, prop.name)
                    self._unique_local_node(node, ancestors=True)
                    values = self._gpio_entries(prop)
                    if not values:
                        rows.append({**base, "index": 0, "port": None, "pin": None, "flags": []})
                    for index, entry in enumerate(values):
                        rows.append({**base, "index": index, **{key: deepcopy(entry[key]) for key in ("port", "pin", "flags")}})
                except SourceError as exc:
                    rows.append({**base, "index": 0, "port": None, "pin": None, "flags": [], "editable": False, "reason": str(exc)})
        return rows

    def plan_gpio(self, node_id, property, index, pin, flags):
        rows = [row for row in self.gpio() if row["node_id"] == node_id and row["property"] == property and row["index"] == index]
        if type(index) is not int or len(rows) != 1 or not rows[0]["editable"]:
            raise SourceError(rows[0]["reason"] if rows else "Choose an editable GPIO assignment from the current observation.")
        if (not isinstance(flags, list) or any(not isinstance(flag, str) or flag not in self.gpio_flags for flag in flags)
                or len(flags) != len(set(flags)) or len(flags) > 5):
            raise SourceError("Choose explicit supported symbolic GPIO flags.")
        if {"GPIO_ACTIVE_HIGH", "GPIO_ACTIVE_LOW"}.issubset(flags) or {"GPIO_PULL_UP", "GPIO_PULL_DOWN"}.issubset(flags):
            raise SourceError("GPIO polarity and pull direction must not conflict.")
        if len(set(flags).intersection({"GPIO_OPEN_DRAIN", "GPIO_OPEN_SOURCE", "GPIO_PUSH_PULL"})) > 1:
            raise SourceError("Choose only one GPIO drive mode.")
        if pin is not None:
            _pin(pin)
            if not flags:
                raise SourceError("Choose GPIO flags explicitly, including active high or active low.")
        document = self.document
        node = document.node(node_id)
        prop = _unique_property(node, property)
        entries = self._gpio_entries(prop)
        if entries and pin is not None and all(entries[index][key] == pin[key] for key in ("port", "pin")) and entries[index]["flags"] == flags:
            return document._plan("nordic-gpio-assign", [])
        if pin is None and not entries:
            return document._plan("nordic-gpio-unassign", [])
        edits = []
        if pin is None:
            if property == "cs-gpios" and len(entries) > 1:
                raise SourceError("Removing a shared chip-select slot would renumber other devices; edit the array and consumers explicitly in source.")
            if len(entries) == 1:
                if _comments(document, prop.start, prop.end):
                    raise SourceError("Removing this GPIO value would remove comments; edit its source explicitly.")
                edits.append(document._edit(prop.start, prop.end, b"/delete-property/ " + property.encode("ascii") + b";"))
            else:
                current = entries[index]
                start = current["separator_start"] if index else current["start"]
                end = current["end"] if index else entries[index + 1]["start"]
                if _comments(document, start, end):
                    raise SourceError("Removing this GPIO entry would remove comments; edit its source explicitly.")
                edits.append(document._edit(start, end, b""))
        else:
            rendered_flags = " | ".join(flags).encode("ascii")
            if len(flags) > 1:
                rendered_flags = b"(" + rendered_flags + b")"
            if entries:
                entry = entries[index]
                for token, rendered in ((entry["provider_token"], ("&gpio" + str(pin["port"])).encode("ascii")),
                                        (entry["pin_token"], str(pin["pin"]).encode("ascii"))):
                    edits.append(document._edit(token.start, token.end, rendered))
                if entry["flags"] != flags:
                    if _comments(document, entry["flags_start"], entry["flags_end"]):
                        raise SourceError("Changing these flags would remove comments; edit their source explicitly.")
                    edits.append(document._edit(entry["flags_start"], entry["flags_end"], rendered_flags))
            else:
                expression = f"<&gpio{pin['port']} {pin['pin']} ".encode("ascii") + rendered_flags + b">"
                edits.append(_replace_property(document, node, property, expression))
        document = _apply(document, edits)
        if pin is not None:
            document = _header(document, _GPIO_HEADER)
        return _finish(self.document, document, "nordic-gpio-unassign" if pin is None else "nordic-gpio-assign")
