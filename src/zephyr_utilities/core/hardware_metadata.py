"""Pure bounded HWMv2 metadata parsing shared by discovery and board observation."""

import re

import yaml


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 40000
MAX_DEPTH = 24
MAX_RECORDS = 20000
MAX_QUALIFIERS = 2000
_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\Z")
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class MetadataError(ValueError):
    pass


class UniqueHardwareLoader(_Loader):
    """Reject duplicate YAML keys instead of silently discarding evidence."""

    def construct_mapping(self, node, deep=False):
        keys = set()
        for key, _ in node.value:
            value = self.construct_object(key, deep=deep)
            if not isinstance(value, str) or value in keys:
                raise MetadataError("Metadata keys must be unique strings.")
            keys.add(value)
        return super().construct_mapping(node, deep=deep)


def parse_hardware_metadata(text):
    """Parse metadata text as data; never read files or execute checkout tooling."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise MetadataError("Hardware metadata exceeds the supported file limit.")
    depth = 0
    try:
        for count, event in enumerate(yaml.parse(text, Loader=_Loader), 1):
            if count > MAX_EVENTS:
                raise MetadataError("Metadata has too many YAML entries.")
            if isinstance(event, yaml.events.AliasEvent):
                raise MetadataError("YAML aliases are unsupported in hardware metadata.")
            if isinstance(event, yaml.events.CollectionStartEvent):
                depth += 1
            elif isinstance(event, yaml.events.CollectionEndEvent):
                depth -= 1
            if depth > MAX_DEPTH:
                raise MetadataError("Metadata nesting exceeds the supported limit.")
        return yaml.load(text, Loader=UniqueHardwareLoader)
    except yaml.YAMLError as exc:
        raise MetadataError(f"Cannot parse hardware metadata: {exc}") from exc


def _mapping(value, allowed, label):
    if not isinstance(value, dict):
        raise MetadataError(f"{label} must be a mapping.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise MetadataError(f"Unsupported {label} fields: {', '.join(sorted(unknown))}.")
    return value


def _sequence(value, label):
    if not isinstance(value, list) or len(value) > MAX_RECORDS:
        raise MetadataError(f"{label} must be a bounded list.")
    return value


def _name(value, label="Name"):
    if not isinstance(value, str) or len(value) > 160 or not _IDENTIFIER.fullmatch(value):
        raise MetadataError(f"{label} must be a simple, nonempty metadata identifier.")
    return value


def _optional_text(value, label):
    if value is not None and (not isinstance(value, str) or len(value) > 1000):
        raise MetadataError(f"{label} must be text.")
    return value


def _clusters(value):
    clusters = []
    for cluster in _sequence(value, "CPU clusters"):
        _mapping(cluster, {"name"}, "CPU cluster")
        clusters.append(_name(cluster.get("name"), "CPU cluster"))
    if len(set(clusters)) != len(clusters):
        raise MetadataError("CPU cluster names are duplicated.")
    return clusters


def soc_entries(data):
    """Return declared SoCs and extensions from supported family/series structures."""
    _mapping(data, {"family", "series", "socs", "vendor", "comment", "runners"}, "SoC document")
    _optional_text(data.get("vendor"), "Vendor")
    _optional_text(data.get("comment"), "Comment")
    if "runners" in data and not isinstance(data["runners"], dict):
        raise MetadataError("Runner metadata must be a mapping.")
    records = []

    def add(socs, family=None, series=None, extensions=False):
        for soc in _sequence(socs, "SoCs"):
            _mapping(soc, {"name", "cpuclusters", "extend"} if extensions else {"name", "cpuclusters"}, "SoC")
            if ("name" in soc) == ("extend" in soc):
                raise MetadataError("A SoC needs either name or extend.")
            records.append({"name": _name(soc.get("name", soc.get("extend")), "SoC"),
                            "family": family, "series": series,
                            "cpuclusters": _clusters(soc.get("cpuclusters", [])),
                            "extension": "extend" in soc})

    def add_series(items, family=None):
        for series in _sequence(items, "SoC series"):
            _mapping(series, {"name", "socs"}, "SoC series")
            add(series.get("socs", []), family, _name(series.get("name"), "SoC series"))

    for family in _sequence(data.get("family", []), "SoC families"):
        _mapping(family, {"name", "series", "socs"}, "SoC family")
        name = _name(family.get("name"), "SoC family")
        add_series(family.get("series", []), name)
        add(family.get("socs", []), name)
    add_series(data.get("series", []))
    add(data.get("socs", []), extensions=True)
    if not any(key in data for key in ("family", "series", "socs")):
        raise MetadataError("SoC document has no supported hardware definitions.")
    return records


def _variants(items, parent, qualifiers, *, depth=0):
    if depth > MAX_DEPTH:
        raise MetadataError("Variant nesting exceeds the supported limit.")
    for variant in _sequence(items, "Variants"):
        _mapping(variant, {"name", "variants", "cpucluster"}, "Variant")
        name = _name(variant.get("name"), "Variant")
        _optional_text(variant.get("cpucluster"), "CPU cluster")
        qualifier = f"{parent}/{name}"
        if len(qualifiers) >= MAX_QUALIFIERS:
            raise MetadataError("Board exceeds the supported qualifier limit.")
        if qualifier in qualifiers:
            raise MetadataError(f"Duplicate qualifier {qualifier}.")
        qualifiers.append(qualifier)
        _variants(variant.get("variants", []), qualifier, qualifiers, depth=depth + 1)


def board_qualifiers(definition, systems):
    """Expand board SoCs, CPU clusters and nested variants into exact qualifiers.

    ``systems`` maps SoC names to already merged metadata records with ``status`` and
    ``cpuclusters``. File discovery, extension merging and freshness stay with callers.
    Board revision and source-filename selection are separate from qualifier expansion.
    """
    if not isinstance(definition, dict) or not isinstance(systems, dict):
        raise MetadataError("Board definition and SoC catalogue must be mappings.")
    names, qualifiers = [], []
    for soc in _sequence(definition.get("socs"), "Board SoCs"):
        _mapping(soc, {"name", "variants"}, "Board SoC")
        soc_name = _name(soc.get("name"), "Board SoC")
        if soc_name in names:
            raise MetadataError(f"Duplicate SoC {soc_name} in board.")
        names.append(soc_name)
        system = systems.get(soc_name)
        if system is None:
            raise MetadataError(f"SoC {soc_name} is missing from the selected roots.")
        if not isinstance(system, dict) or system.get("status") != "discovered":
            raise MetadataError(f"SoC {soc_name} has conflicting definitions.")
        clusters = _sequence(system.get("cpuclusters"), "CPU clusters")
        for cluster in clusters:
            _name(cluster, "CPU cluster")
        if len(set(clusters)) != len(clusters):
            raise MetadataError("CPU cluster names are duplicated.")
        variants = _sequence(soc.get("variants", []), "Variants")
        if clusters:
            for variant in variants:
                if not isinstance(variant, dict) or variant.get("cpucluster") not in clusters:
                    raise MetadataError(f"Variant needs a known CPU cluster of {soc_name}.")
            for cluster in clusters:
                base = f"{soc_name}/{cluster}"
                if len(qualifiers) >= MAX_QUALIFIERS:
                    raise MetadataError("Board exceeds the supported qualifier limit.")
                qualifiers.append(base)
                _variants([variant for variant in variants if variant["cpucluster"] == cluster], base, qualifiers)
        else:
            if any(isinstance(variant, dict) and "cpucluster" in variant for variant in variants):
                raise MetadataError(f"SoC {soc_name} declares no CPU clusters for these variants.")
            if len(qualifiers) >= MAX_QUALIFIERS:
                raise MetadataError("Board exceeds the supported qualifier limit.")
            qualifiers.append(soc_name)
            _variants(variants, soc_name, qualifiers)
    if not names:
        raise MetadataError("Board declares no SoCs.")
    return qualifiers
