"""Resolve an exact HWMv2 target from current captured metadata, without CMake."""

import re

from ...core.hardware_metadata import board_qualifiers, parse_hardware_metadata, soc_entries


IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,159}\Z")


def _systems(sources, files, wanted):
    systems, extensions = {}, []
    for path in files:
        if path.name != "soc.yml":
            continue
        for record in soc_entries(parse_hardware_metadata(sources.read(path))):
            if record["name"] not in wanted:
                continue
            if record["extension"]:
                extensions.append(record)
            elif record["name"] in systems:
                raise ValueError("The selected SoC metadata has conflicting base definitions. Analyze the environment again.")
            else:
                systems[record["name"]] = {**record, "status": "discovered"}
    for extension in extensions:
        system = systems.get(extension["name"])
        if system is None:
            raise ValueError("A selected SoC metadata extension has no base definition.")
        if set(system["cpuclusters"]) & set(extension["cpuclusters"]):
            raise ValueError("The selected SoC metadata repeats CPU clusters in an extension.")
        system["cpuclusters"] += extension["cpuclusters"]
    return systems


def _architecture(sources, board, target, directory):
    # Platform files are top-level only; an empty prefix excludes all child directories.
    files = sources.reader.list_files(directory, {".yaml"}, max_files=1000, max_dirs=1, exclude_prefixes=("",))
    sources.platform_files = files
    captured = board.get("platforms", [])
    if (not isinstance(captured, list) or len(captured) > 1000
            or any(not isinstance(item, dict) or not isinstance(item.get("target"), str)
                   or not isinstance(item.get("source_file"), str) for item in captured)):
        raise ValueError("The captured board platform metadata is invalid. Analyze the environment again.")
    current = []
    for path in files:
        data = parse_hardware_metadata(sources.read(path))
        identifier = data.get("identifier") if isinstance(data, dict) else None
        if isinstance(identifier, str) and (identifier == board["name"] or identifier.startswith((board["name"] + "/", board["name"] + "@"))):
            architecture = data.get("arch")
            if architecture is not None and (not isinstance(architecture, str) or not IDENTIFIER.fullmatch(architecture)):
                raise ValueError("Current platform architecture metadata is invalid.")
            current.append({"target": identifier, "architecture": architecture, "source_file": str(path)})
    captured = [{key: item.get(key) for key in ("target", "architecture", "source_file")} for item in captured]
    if current != sorted(captured, key=lambda item: item["source_file"]):
        raise ValueError("The board platform metadata changed. Analyze the environment again.")
    exact = [item for item in current if item["target"] == target]
    architectures = {item["architecture"] for item in exact}
    origin = "target-platform"
    if not exact:
        architectures = {item["architecture"] for item in current if item["architecture"] is not None}
        if architectures != set(board.get("architectures") or []):
            raise ValueError("The board architecture metadata changed. Analyze the environment again.")
        origin = "board-single-architecture"
    if len(architectures) != 1:
        raise ValueError("The selected target needs unambiguous architecture metadata; other cores' architectures cannot be substituted.")
    architecture = next(iter(architectures))
    if not isinstance(architecture, str) or not IDENTIFIER.fullmatch(architecture):
        raise ValueError("The selected target's architecture metadata is missing or unsupported.")
    return architecture, origin


def resolve_board_selection(sources, context, board, directory):
    target = context.get("target") or {}
    qualifiers = target.get("qualifiers")
    if (not isinstance(qualifiers, list) or not qualifiers or len(qualifiers) > 24
            or any(not isinstance(part, str) or not IDENTIFIER.fullmatch(part) for part in qualifiers)
            or not isinstance(board.get("name"), str) or not IDENTIFIER.fullmatch(board["name"])):
        raise ValueError("Choose an exact board target with supported SoC/core/variant qualifiers.")
    requested = board["name"] + "/" + "/".join(qualifiers)
    if target.get("requested") != requested or target.get("board") != board["name"]:
        raise ValueError("The exact target and its board/qualifier metadata do not agree.")
    current = parse_hardware_metadata(sources.read(directory / "board.yml"))
    if not isinstance(current, dict) or ("board" in current) == ("boards" in current):
        raise ValueError("Current board metadata needs one board or boards declaration.")
    if set(current) - {"board", "boards", "runners"} or "runners" in current and not isinstance(current["runners"], dict):
        raise ValueError("Current board document metadata has unsupported fields.")
    records = [current["board"]] if "board" in current else current["boards"]
    if not isinstance(records, list):
        raise ValueError("Current boards metadata must be a list.")
    if any(isinstance(record, dict) and record.get("extend") == board["name"] for record in records):
        raise ValueError("Board extension metadata needs explicit composition support before hardware discovery.")
    selected = [record for record in records if isinstance(record, dict) and record.get("name") == board["name"]]
    if len(selected) != 1:
        raise ValueError("The current board metadata no longer uniquely declares the captured board. Analyze the environment again.")
    definition = selected[0]
    if set(definition) - {"name", "full_name", "vendor", "revision", "socs"}:
        raise ValueError("Current selected board metadata has unsupported fields.")
    if definition.get("revision") is not None:
        raise ValueError("Board revision composition requires a separate discovery adapter.")
    declared = definition.get("socs")
    if not isinstance(declared, list) or any(not isinstance(soc, dict) for soc in declared):
        raise ValueError("Current board SoC metadata is invalid.")
    names = [soc.get("name") for soc in declared]
    if any(not isinstance(name, str) for name in names) or set(names) != set(board.get("socs", [])):
        raise ValueError("Current board SoC declarations differ from the captured metadata. Analyze the environment again.")
    files = list(dict.fromkeys(sources.reader.path(path) for path in board.get("source_files", [])))
    for path in files:
        if path.name == "board.yml" and path != directory / "board.yml":
            raise ValueError("Additional board metadata needs explicit extension/composition support before hardware discovery.")
        sources.read(path)
    systems = _systems(sources, files, set(names))
    live = board_qualifiers(definition, systems)
    captured = board.get("qualifiers")
    if not isinstance(captured, list) or set(live) != set(captured) or "/".join(qualifiers) not in live:
        raise ValueError("Current board/SoC qualifiers differ from the captured target metadata. Analyze the environment again.")
    full = "_".join([board["name"], *qualifiers])
    short = "_".join([board["name"], *qualifiers[1:]])
    candidates = list(dict.fromkeys(directory / (name + ".dts") for name in (full, short)))
    found = [path for path in candidates if sources.exists(path)]
    if directory / (short + ".dts") in found and full != short and len(names) != 1:
        raise ValueError("A shortened DTS filename is not allowed for a board with multiple SoCs, even when a full filename exists.")
    if len(found) != 1:
        raise ValueError("The exact board DTS filename is missing or ambiguous; no core/variant fallback is guessed.")
    for name in {full, short}:
        if sources.exists(directory / (name + ".overlay")):
            raise ValueError("An additional board overlay needs explicit composition support before hardware discovery.")
    architecture, origin = _architecture(sources, board, requested, directory)
    return {"target": requested, "qualifier": "/".join(qualifiers), "board_source": str(found[0]),
            "architecture": architecture, "architecture_origin": origin,
            "metadata_files": sorted({str(directory / "board.yml"), *(str(path) for path in files),
                                      *(str(path) for path in sources.platform_files)})}
