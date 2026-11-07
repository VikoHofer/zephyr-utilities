"""Connector GPIO ownership follows source map masks and pass-through flags."""

from copy import deepcopy
import unittest

from zephyr_utilities.features.devicetree.chip_gpio import resolve_gpio


def topology():
    provider = {"id": "hardware:/gpio1", "path": "/gpio1", "labels": ["gpio1"], "reference": "&gpio1",
                "binding": {"state": "matched"}, "supported": True, "cells": ["pin", "flags"], "pin_count": 16}
    connector = {"id": "hardware:/connector", "path": "/connector", "labels": ["arduino_header", "header"],
                 "reference": "&arduino_header", "binding": {"state": "matched"}, "properties": {"#gpio-cells": 2}}
    mapping = {"id": connector["id"], "path": connector["path"], "mask": [0xffffffff, 0xffffffc0], "pass_thru": [0, 0x3f],
               "entries": [{"child_cells": [16, 0], "provider_id": provider["id"],
                            "provider_path": provider["path"], "parent_cells": [12, 0]}]}
    return {"nodes": [deepcopy(provider), connector], "gpio_providers": [provider], "connector_maps": [mapping]}


class ChipGpioResolutionTests(unittest.TestCase):
    def test_direct_provider_labels_and_absolute_references(self):
        payload = topology()
        for reference in ("&gpio1", "&{/gpio1}"):
            with self.subTest(reference=reference):
                provider, cells = resolve_gpio(payload, reference, [12, 1])
                self.assertIs(provider, payload["gpio_providers"][0])
                self.assertEqual(cells, (12, 1))

    def test_nordic_arduino_d10_resolves_to_p1_12_with_active_low(self):
        payload = topology()
        original = deepcopy(payload)
        for reference in ("&arduino_header", "&header", "&{/connector}"):
            with self.subTest(reference=reference):
                provider, cells = resolve_gpio({"payload": payload}, reference, (16, 1))
                self.assertEqual(provider["labels"], ["gpio1"])
                self.assertEqual(cells, (12, 1))
        self.assertEqual(payload, original)

    def test_pass_through_replaces_bits_and_keeps_fixed_parent_flags(self):
        payload = topology()
        payload["connector_maps"][0]["entries"][0]["parent_cells"][1] = 0x9f
        self.assertEqual(resolve_gpio(payload, "&header", [16, 0x21])[1], (12, 0xa1))
        payload["connector_maps"][0]["mask"][0] = 0xfffffff0
        payload["connector_maps"][0]["pass_thru"][0] = 3
        self.assertEqual(resolve_gpio(payload, "&header", [17, 1])[1], (13, 0x81))

    def test_mask_is_applied_to_input_only_not_to_mapping_rows(self):
        payload = topology()
        payload["connector_maps"][0]["entries"][0]["child_cells"][1] = 1
        with self.assertRaisesRegex(ValueError, "no unique matching"):
            resolve_gpio(payload, "&header", [16, 1])

    def test_absent_mask_and_pass_through_use_exact_parent_mapping(self):
        payload = topology()
        mapping = payload["connector_maps"][0]
        mapping.pop("mask")
        mapping.pop("pass_thru")
        mapping["entries"][0]["parent_cells"][1] = 2
        self.assertEqual(resolve_gpio(payload, "&header", [16, 0])[1], (12, 2))
        with self.assertRaises(ValueError):
            resolve_gpio(payload, "&header", [16, 1])

    def test_duplicate_missing_and_undecoded_maps_fail_closed(self):
        for mutate in (lambda value: value["entries"].append(deepcopy(value["entries"][0])),
                       lambda value: value.update(entries=[]),
                       lambda value: value.update(reason="Undecoded map"),
                       lambda value: value["entries"][0].update(child_cells=[15, 0])):
            payload = topology()
            mutate(payload["connector_maps"][0])
            with self.subTest(mapping=payload["connector_maps"]), self.assertRaises(ValueError):
                resolve_gpio(payload, "&header", [16, 1])

    def test_invalid_width_signed_boolean_and_large_cells_fail_closed(self):
        for cells in ([12], [12, 0, 0], [-1, 0], [True, 0], [12, -1], [12, 0x100000000]):
            with self.subTest(cells=cells), self.assertRaises(ValueError):
                resolve_gpio(topology(), "&gpio1", cells)
        for key in ("mask", "pass_thru"):
            payload = topology()
            payload["connector_maps"][0][key] = [0]
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve_gpio(payload, "&header", [16, 1])

    def test_unknown_binding_out_of_range_and_mismatched_provider_evidence_fail(self):
        for mutate in (lambda value: value["nodes"][1].update(binding={"state": "unknown"}),
                       lambda value: value["nodes"][1]["properties"].update({"#gpio-cells": 3}),
                       lambda value: value["gpio_providers"][0].update(supported=False),
                       lambda value: value["gpio_providers"][0].update(pin_count=12),
                       lambda value: value["connector_maps"][0]["entries"][0].update(provider_path="/other")):
            payload = topology()
            mutate(payload)
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                resolve_gpio(payload, "&header", [16, 1])

    def test_unknown_ambiguous_or_chained_references_fail_closed(self):
        payload = topology()
        with self.assertRaises(ValueError):
            resolve_gpio(payload, "&missing", [16, 1])
        payload["nodes"].append({**payload["nodes"][1], "id": "hardware:/other", "path": "/other"})
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_gpio(payload, "&header", [16, 1])
        payload = topology()
        payload["connector_maps"][0]["entries"][0].update(provider_id="hardware:/connector", provider_path="/connector")
        with self.assertRaisesRegex(ValueError, "chained"):
            resolve_gpio(payload, "&header", [16, 1])
        payload = topology()
        payload["connector_maps"].append({"id": "hardware:/gpio1", "path": "/gpio1", "entries": []})
        with self.assertRaisesRegex(ValueError, "cyclic"):
            resolve_gpio(payload, "&header", [16, 1])


if __name__ == "__main__":
    unittest.main()
