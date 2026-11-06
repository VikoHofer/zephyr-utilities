"""Isolated selected-Zephyr DTS parsing worker; input/output are bounded JSON files."""

import json
import os
from pathlib import Path
import resource
import sys


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    if "command" in request:
        os.execv(request["command"][0], request["command"])
    sys.path.insert(0, request["library"])
    from devicetree import dtlib  # Selected, fingerprinted Zephyr tool, never a report module.

    tree = dtlib.DT(request["source"])
    nodes = []
    for node in tree.node_iter():
        if len(nodes) >= 4096:
            raise ValueError("Board topology exceeds 4096 nodes.")
        nodes.append({"path": node.path, "source": {"path": node.filename, "line": node.lineno},
                      "property_sources": {name: {"path": prop.filename, "line": prop.lineno}
                                           for name, prop in node.props.items()}})
    maps = []
    for node in tree.node_iter():
        prop = node.props.get("gpio-map")
        if prop is None:
            continue
        try:
            count = node.props["#gpio-cells"].to_num()
            values = dtlib.to_nums(prop.value)
            entries, offset = [], 0
            while offset < len(values):
                child = values[offset:offset + count]
                provider = tree.phandle2node[values[offset + count]]
                parent_count = provider.props["#gpio-cells"].to_num()
                parent = values[offset + count + 1:offset + count + 1 + parent_count]
                if len(child) != count or len(parent) != parent_count or not 0 < count <= 16 or not 0 < parent_count <= 16:
                    raise ValueError("Unsupported GPIO connector map layout.")
                entries.append({"child_cells": child, "provider_path": provider.path, "parent_cells": parent})
                offset += count + 1 + parent_count
            maps.append({"path": node.path, "entries": entries,
                         "mask": node.props["gpio-map-mask"].to_nums() if "gpio-map-mask" in node.props else None,
                         "pass_thru": node.props["gpio-map-pass-thru"].to_nums() if "gpio-map-pass-thru" in node.props else None})
        except (KeyError, IndexError, ValueError, dtlib.DTError):
            maps.append({"path": node.path, "entries": [], "reason": "This connector mapping is outside the decoded GPIO layout."})
    result = {"source": str(tree), "nodes": nodes, "connector_maps": maps}
    encoded = json.dumps(result)
    if len(encoded.encode()) > 4 * 1024 * 1024:
        raise ValueError("Board topology worker output exceeds 4 MiB.")
    Path(request["output"]).write_text(encoded)


if __name__ == "__main__":
    main()
