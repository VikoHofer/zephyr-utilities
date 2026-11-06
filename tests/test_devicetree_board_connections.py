"""Inherited connections edit only the guarded overlay in one canonical action."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.board_connections import BoardConnectionService, connection_options
from zephyr_utilities.features.devicetree.board_sources import BoardSources
import test_devicetree_board_topology as topology_fixture


RECIPES = {
    "bme280.yaml": '''compatible: bosch,bme280
include: base.yaml
on-bus: i2c
properties:
  reg: {type: array, required: true}
''',
    "leds.yaml": '''compatible: gpio-leds
include: base.yaml
child-binding:
  properties:
    gpios: {type: phandle-array, required: true}
''',
}


class BoardConnectionTests(unittest.TestCase):
    binding = topology_fixture.BoardTopologyTests.binding
    guard = topology_fixture.BoardTopologyTests.guard
    worker = topology_fixture.BoardTopologyTests.worker

    def setUp(self):
        topology_fixture.BoardTopologyTests.setUp(self)
        self.tree = topology_fixture.TREE.replace('status = "disabled"', 'status = "okay"')
        for name, value in RECIPES.items():
            (self.bindings / name).write_text(value)
        self.drafts.board_connections = BoardConnectionService(self.drafts, self.board)

    def inspect(self, text=None, *, maps=None):
        worker = self.worker(text or self.tree)
        worker['connector_maps'] = maps or []
        with patch.object(BoardSources, 'run', return_value=worker):
            self.payload = self.board.inspect(self.guard())
        self.assertTrue(self.payload['supported'], self.payload['reason'])
        return self.payload

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))

    def option(self, kind):
        return next(option for options in self.payload['connection_options'].values() for option in options if option['kind'] == kind)

    def add(self, kind='i2c', **changes):
        connection = ({'kind': 'i2c', 'controller_id': 'hardware:/i2c', 'compatible': 'bosch,bme280',
                       'address': 0x76, 'enable': False, 'name': 'bme280', 'label': ''} if kind == 'i2c' else
                      {'kind': 'gpio-led', 'controller_id': 'hardware:/gpio', 'pin': 3, 'flags': 1, 'name': 'led', 'label': ''})
        connection.update(changes)
        self.draft = self.drafts.command(self.guard(command={'kind': 'board_module_add',
                                        'observation_id': self.payload['observation_id'], 'connection': connection}))
        return self.draft

    def test_blank_overlay_i2c_add_is_single_undo_and_does_not_copy_board_nodes(self):
        before, board = self.draft['text'], self.board_file.read_bytes()
        self.inspect()
        self.add(label='environment')
        after = self.draft['text']
        self.assertIn('&i2c0 {', after)
        self.assertIn('environment: bme280@76', after)
        self.assertNotIn('test,i2c', after)
        self.assertNotIn('aliases {', after)
        self.assertEqual(self.draft['revision'], 1)
        self.assertEqual(self.overlay.read_text(), before)
        self.assertEqual(self.board_file.read_bytes(), board)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft['text'], before)
        self.assertFalse(self.draft['can_undo'])
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft['text'], after)

    def test_path_only_controller_reference_and_explicit_enable(self):
        self.inspect(self.tree.replace('i2c0: ', '').replace('status = "okay"; };\n};', 'status = "disabled"; };\n};'))
        self.assertTrue(self.option('i2c')['enable_required'])
        with self.assertRaisesRegex(ValueError, 'enable'):
            self.add()
        self.add(enable=True)
        self.assertIn('&{/i2c}', self.draft['text'])
        self.assertIn('status = "okay"', self.draft['text'])

    def test_reuses_single_existing_reference_and_preserves_comments(self):
        before = '/* preserve this exact note */\n&i2c0 {\n\tclock-frequency = <100000>; /* routed already */\n};\n'
        self.source(before)
        self.inspect()
        self.add()
        self.assertEqual(self.draft['text'].count('&i2c0'), 1)
        self.assertIn('\tclock-frequency = <100000>; /* routed already */', self.draft['text'])
        self.assertTrue(self.draft['text'].startswith('/* preserve this exact note */'))

    def test_multiple_target_contributors_only_withhold_that_action(self):
        for source in ('&i2c0 { };\n&{/i2c} { };\n', '&i2c0 { };\n&i2c0 { };\n'):
            with self.subTest(source=source):
                self.source(source)
                self.inspect()
                self.assertFalse(self.option('i2c')['addable'])
                self.assertTrue(self.option('gpio-led')['addable'])
                with self.assertRaises(ValueError):
                    self.add()

    def test_existing_child_address_and_global_label_collisions_are_preserved(self):
        tree = self.tree.replace('status = "okay"; };\n};', 'status = "okay"; existing: sensor@76 { reg = <0x76>; }; };\n};')
        self.inspect(tree)
        self.assertEqual(self.option('i2c')['occupied_addresses'], [0x76])
        for fields in ({}, {'address': 0x77, 'label': 'existing'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.add(**fields)
        self.add(address=0x77)

    def test_unknown_i2c_address_or_missing_required_routing_withholds_i2c(self):
        self.inspect(self.tree.replace('status = "okay"; };\n};', 'status = "okay"; odd { reg = <2>; }; };\n};'))
        self.assertFalse(self.option('i2c')['addable'])
        (self.bindings / 'i2c.yaml').write_text(topology_fixture.BINDINGS['i2c.yaml'] + '  pinctrl-0: {type: phandles, required: true}\n')
        self.inspect()
        self.assertIn('routing', self.option('i2c')['reason'])

    def test_gpio_led_creates_root_group_without_an_alias_and_is_atomic(self):
        before = self.draft['text']
        self.inspect(self.tree.replace('gpioa: ', ''))
        self.add('gpio-led', flags=0x8101, label='indicator')
        self.assertIn('app-leds', self.draft['text'])
        self.assertIn('compatible = "gpio-leds"', self.draft['text'])
        self.assertIn('indicator: led', self.draft['text'])
        self.assertIn('&{/gpio}', self.draft['text'])
        self.assertNotIn('aliases {', self.draft['text'])
        document = self.drafts.drafts[self.draft['id']].document
        value = document.property_value(document.find_node('led').properties[0].id)
        self.assertEqual(value.value[0][1], (3, 0x8101))
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft['text'], before)
        self.assertFalse(self.draft['can_undo'])

    def test_gpio_known_consumers_reservations_and_hogs_exclude_pins(self):
        tree = self.tree.replace('gpio-controller;', 'gpio-controller; gpio-reserved-ranges = <2 2>;')
        tree = tree.replace('status = "okay"; };', 'status = "okay"; hog { gpio-hog; gpios = <5 0>; }; };', 1)
        tree = tree.replace('compatible = "test,board";', 'compatible = "test,board"; leds { compatible = "gpio-leds"; first { gpios = <&gpioa 6 0>; }; };', 1)
        self.inspect(tree)
        self.assertEqual(self.option('gpio-led')['occupied_pins'], [5, 6])
        self.assertTrue(any(item['kind'] == 'gpio' and item['pin'] == 6 and item['owner_id'] == 'hardware:/leds/first'
                            for item in self.payload['allocations']))
        for pin in (2, 3, 5, 6, 16, -1, True):
            with self.subTest(pin=pin), self.assertRaises(ValueError):
                self.add('gpio-led', pin=pin)
        self.add('gpio-led', pin=7)

    def test_connector_map_is_association_not_pin_occupation(self):
        tree = self.tree.replace('compatible = "test,board";', 'compatible = "test,board"; connector { gpio-map = <&gpioa 3 0>; };', 1)
        maps = [{'path': '/connector', 'entries': [{'provider_path': '/gpio', 'cells': [3, 0], 'child_cells': [1, 0]}]}]
        self.inspect(tree, maps=maps)
        self.assertTrue(self.option('gpio-led')['addable'], self.option('gpio-led')['reason'])
        self.assertNotIn(3, self.option('gpio-led')['occupied_pins'])
        self.add('gpio-led')

    def test_unknown_gpio_reference_withholds_gpio_without_blocking_i2c(self):
        self.inspect(self.tree.replace('compatible = "test,board";', 'compatible = "test,board"; mystery { route = <&gpioa 3 0>; };', 1))
        self.assertFalse(self.option('gpio-led')['addable'])
        self.assertTrue(self.option('i2c')['addable'])
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.add('gpio-led')

    def test_known_pinctrl_allocation_is_not_available_as_led_pin(self):
        self.inspect()
        observed = self.board.observations[self.payload['observation_id']]
        observed['payload']['allocations'].append({'provider_id': 'hardware:/gpio', 'pin': 3, 'kind': 'pinctrl',
                                                   'owner_id': 'hardware:/i2c', 'state': 'default', 'role': 'sda'})
        with self.assertRaises(ValueError):
            self.add('gpio-led')

    def test_unknown_family_pin_routing_withholds_gpio_only(self):
        self.inspect()
        observed = self.board.observations[self.payload['observation_id']]
        observed['payload']['pin_routing_complete'] = False
        with self.assertRaisesRegex(ValueError, 'pin routing'):
            self.add('gpio-led')
        self.add()

    def test_generic_gpio_allocations_do_not_duplicate_on_repeated_capabilities(self):
        tree = self.tree.replace('compatible = "test,board";', 'compatible = "test,board"; leds { compatible = "gpio-leds"; first { gpios = <&gpioa 6 0>; }; };')
        self.inspect(tree)
        observed = self.board.observations[self.payload['observation_id']]
        before = deepcopy(observed['payload']['allocations'])
        for _ in range(3):
            connection_options(observed['document'], observed['projection'], observed['catalog'], observed['payload'],
                               self.drafts.drafts[self.draft['id']].document)
        self.assertEqual(observed['payload']['allocations'], before)

    def test_connector_consumers_do_not_establish_free_provider_pins(self):
        tree = self.tree.replace('compatible = "test,board";', 'compatible = "test,board"; connector: connector { gpio-map = <&gpioa 3 0>; }; consumer { gpios = <&connector 1 0>; };')
        maps = [{'path': '/connector', 'entries': [{'provider_path': '/gpio', 'parent_cells': [3, 0], 'child_cells': [1, 0]}]}]
        self.inspect(tree, maps=maps)
        self.assertFalse(self.option('gpio-led')['addable'])
        self.assertTrue(self.option('i2c')['addable'])

    def test_existing_led_group_gets_only_new_child_and_preserves_handwritten_one(self):
        source = '/ { app-leds { compatible = "gpio-leds"; /* own note */ old { gpios = <&gpioa 4 0>; }; }; };\n'
        self.source(source)
        tree = self.tree.replace('compatible = "test,board";', 'compatible = "test,board"; app-leds { compatible = "gpio-leds"; old { gpios = <&gpioa 4 0>; }; };')
        self.inspect(tree)
        self.add('gpio-led')
        self.assertEqual(self.draft['text'].count('app-leds'), 1)
        self.assertIn('/* own note */ old { gpios = <&gpioa 4 0>; };', self.draft['text'])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft['text'], source)

    def test_gpio_range_cells_and_status_must_be_established(self):
        for replacement in ('gpio-controller = <1>;', 'gpio-controller; ngpios = <BAD>;', 'gpio-controller; ngpios = <0>;'):
            with self.subTest(replacement=replacement):
                self.inspect(self.tree.replace('gpio-controller;', replacement))
                self.assertFalse(self.option('gpio-led')['addable'])
        self.inspect(self.tree.replace('#gpio-cells = <2>', '#gpio-cells = <1>'))
        self.assertFalse(self.option('gpio-led')['addable'])
        self.inspect(topology_fixture.TREE)
        self.assertFalse(self.option('gpio-led')['addable'])

    def test_led_group_path_collision_does_not_replace_existing_content(self):
        self.inspect(self.tree.replace('compatible = "test,board";', 'compatible = "test,board"; app-leds { compatible = "unknown,thing"; };', 1))
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.add('gpio-led')
        self.assertTrue(self.option('i2c')['addable'])

    def test_required_extra_recipe_properties_withhold_only_that_recipe(self):
        (self.bindings / 'bme280.yaml').write_text(RECIPES['bme280.yaml'] + '  power-mode: {type: int, required: true}\n')
        self.inspect()
        self.assertFalse(self.option('i2c')['addable'])
        self.assertTrue(self.option('gpio-led')['addable'])

    def test_source_or_binding_changes_invalidate_connection_before_mutation(self):
        self.inspect()
        original = deepcopy(self.draft)
        (self.bindings / 'bme280.yaml').write_text(RECIPES['bme280.yaml'] + '  optional: {type: int}\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.add()
        self.assertEqual(self.draft, original)
        self.inspect()
        self.source('/* new revision */\n')
        with self.assertRaisesRegex(ValueError, 'earlier'):
            self.add()

    def test_numeric_fields_and_exact_command_shape_are_validated(self):
        self.inspect()
        for changes in ({'address': '0x76'}, {'address': True}, {'enable': 1}, {'name': 'bme@76'}, {'unexpected': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.add(**changes)
        for flags in (True, -1, 65536, '1'):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.add('gpio-led', flags=flags)
