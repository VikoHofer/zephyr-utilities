// Informational evidence shipped with this version. Runtime support is source-derived;
// this catalogue must never become an activation allowlist.
const automaticEvidence = "docs/device-tree-editor-automatic-chip-support-handoff.md";
const revisionEvidence = "docs/device-tree-editor-board-revisions-handoff.md";
const nrf54Evidence = "docs/device-tree-editor-nrf54l-handoff.md";
const features = ["GPIO", "UART", "I2C", "SPI"];
const saved = "GPIO, UART, I2C and SPI changes saved; changed commands exercised Undo/Redo.";
const notBuilt = "Not run in the recorded matrix";

function board(name, vendor, soc, target, options = {}) {
  return {name, vendor, soc, target, features: [...features], verification: saved,
    build: "Passed", notes: [], evidence: automaticEvidence, ...options};
}

export const supportCatalog = {
  updated: "2026-11-07",
  scope: "This version includes STM32 and Nordic pin adapters. The boards below have recorded source-level workflow checks; other boards can work automatically when their selected DTS, bindings and pin tables use understood formats.",
  evidenceNotice: "Recorded checks cover overlay generation and, where shown, Configure/build. No physical hardware operation was validated. Available pins and actions still depend on the selected project's sources, package, revision, ownership and existing assignments.",
  verifiedBoards: [
    board("Nucleo F446RE", "STM32", "STM32F446RE", "nucleo_f446re/stm32f446xx", {
      verification: "GPIO, UART and SPI changes saved with Undo/Redo; the existing I2C route was retained unchanged.",
      build: notBuilt,
    }),
    board("Nucleo F401RE", "STM32", "STM32F401RE", "nucleo_f401re/stm32f401xe"),
    board("Nucleo F411RE", "STM32", "STM32F411RE", "nucleo_f411re/stm32f411xe", {build: notBuilt}),
    board("Nucleo G071RB", "STM32", "STM32G071RB", "nucleo_g071rb/stm32g071xx", {
      verification: "GPIO, LPUART, I2C and SPI changes saved and reopened; changed commands exercised Undo/Redo.",
    }),
    board("Nucleo L476RG", "STM32", "STM32L476RG", "nucleo_l476rg/stm32l476xx", {
      verification: "GPIO, USART and I2C changes saved and reopened with Undo/Redo; the existing SPI route was retained unchanged.",
    }),
    board("Nucleo H743ZI", "STM32", "STM32H743ZI", "nucleo_h743zi/stm32h743xx"),
    board("Nucleo U575ZI-Q", "STM32", "STM32U575ZI", "nucleo_u575zi_q/stm32u575xx"),
    board("nRF51 DK", "Nordic", "nRF51822", "nrf51dk/nrf51822"),
    board("nRF52 DK", "Nordic", "nRF52832", "nrf52dk/nrf52832"),
    board("nRF52833 DK", "Nordic", "nRF52833", "nrf52833dk/nrf52833"),
    board("nRF52840 DK", "Nordic", "nRF52840", "nrf52840dk/nrf52840", {build: notBuilt}),
    board("nRF5340 DK", "Nordic", "nRF5340", "nrf5340dk/nrf5340/cpuapp", {
      notes: ["Secure application core only. Network-core and non-secure ownership are not implemented."],
    }),
    board("nRF9160 DK", "Nordic", "nRF9160", "nrf9160dk/nrf9160", {
      verification: "All four functions passed Undo/Redo, first overlay creation, Save and saved-source reread.",
      notes: ["Checked with automatic default revision 0.14.0 and explicit nrf9160dk@0.7.0/nrf9160. Defaults follow the selected Zephyr checkout.",
        "Non-secure /ns pin activation remains unavailable."],
      evidence: revisionEvidence,
    }),
    board("nRF9161 DK", "Nordic", "nRF9161", "nrf9161dk/nrf9161", {
      verification: "All four functions passed Undo/Redo, first overlay creation, Save and saved-source reread.",
      notes: ["Checked with automatic default revision 0.9.0 and explicit nrf9161dk@0.7.0/nrf9161. Defaults follow the selected Zephyr checkout.",
        "Non-secure /ns pin activation remains unavailable."],
      evidence: revisionEvidence,
    }),
    board("nRF54L15 DK", "Nordic", "nRF54L15", "nrf54l15dk/nrf54l15/cpuapp", {
      verification: "All four functions passed Undo/Redo, first overlay creation, Save and saved-source reread.",
      notes: ["Secure CPUAPP native-domain routes only; clock and dedicated signal restrictions apply. An active FLPR coprocessor blocks activation.",
        "SPI00 drive validation covers devices at 8 MHz or below. Unknown frequency, higher speeds and unsupported extra-high-drive states remain unavailable.",
        "The diagram shows logical GPIO slots, not confirmed package pads. P2 has no GPIO interrupt/SENSE functionality."],
      evidence: nrf54Evidence,
    }),
  ],
  automaticCoverage: [
    {
      vendor: "STM32",
      title: "STM32 package pin tables",
      description: "The selected STM32_PINMUX package table supplies GPIO and UART/I2C/SPI alternatives automatically. No per-chip catalogue entry is needed. One package table was audited from each of F0, F2, F3, F4, F7, G0, G4, H5, H7, L0, L1, L4, L5, U0, U3, U5, WB, WBA, WL, C0 and N6.",
      limits: ["Family table audits are not complete board workflows or coverage of every package. The audited N6 package did not provide a verified I2C1 SCL route.",
        "STM32F1 remap encoding and specialized H5/N6 pin states remain guarded. Ambiguous tables, changed encodings and unresolved reservations block affected actions.",
        "Package tables may omit oscillator pad identities. Physical layout and board wiring are not inferred."],
    },
    {
      vendor: "Nordic",
      title: "Nordic GPIO and PSEL sources",
      description: "nRF51, nRF52, nRF53 and nRF91 use shared GPIO port/count/reservation and NRF_PSEL discovery when the selected primary-core routing and ownership model is understood. nRF52810 also has synthetic adapter coverage, without a recorded full board workflow.",
      limits: ["Logical GPIO slots do not establish package bonding or header wiring. Reservations, existing users, shared peripheral instances and source states constrain assignments.",
        "Non-secure and secondary-core ownership, forwarding and UICR-v2 models remain unavailable. Low-frequency crystal use depends on Kconfig clock selection, which the editor does not resolve."],
    },
    {
      vendor: "Nordic",
      title: "nRF54L shared architecture",
      description: "The shared nRF54L05/L10/L15 architecture is recognized automatically, with secure CPUAPP native-domain rules. nRF54L15 DK has recorded board workflow evidence; nRF54L05 and nRF54L10 have synthetic adapter tests only.",
      limits: ["Serial 20/21/22 use P1, serial 30 uses P0, and serial 00 uses P2. Clock and dedicated pin choices remain restricted.",
        "Cross-domain routing, non-secure images, secondary cores, active FLPR, unknown register layouts and UICR ownership require further implementation."],
    },
  ],
  unavailable: [
    {name: "Espressif", reason: "Pin activation adapter not implemented. ESP32 remains an acceptance target."},
    {name: "NXP", reason: "Pin activation adapter not implemented. K64 remains an acceptance target."},
    {name: "Raspberry Pi", reason: "Pin activation adapter not implemented. RP2040 remains an acceptance target."},
    {name: "STM32F1", reason: "Its remap encoding needs a dedicated adapter; standard STM32_PINMUX coverage does not apply."},
    {name: "Nordic nRF54H", reason: "Its routing and ownership model is not implemented. The nRF54H20 DK comparison also exceeded the current pin alternative limit."},
    {name: "Non-secure and secondary-core targets", reason: "Nordic pin forwarding and controlling-core permissions are not established by the current source observation."},
    {name: "Custom board composition", reason: "Standard declared default and explicit revisions resolve automatically. Custom revision.cmake scripts and board extensions need additional discovery support."},
    {name: "Advanced peripherals and device connections", reason: "This stage covers GPIO, UART, I2C and SPI pin activation. Advanced peripherals and sensor/module composition are outside its scope."},
  ],
  contributing: {
    intro: "Start with one exact board target and a reproducible missing capability. A new board using an understood source format can already work without adding it to this list; a new encoding or ownership model needs an adapter.",
    steps: [
      "Record the exact target, revision/core, Zephyr version, selected DTS/binding/pin-table inputs and the editor's unavailable reason. Use a small sanitized application.",
      "Extend automatic source discovery and the relevant family adapter. For a new brand, implement its verified routing, reservations and ownership rules; keep unknown cases visibly unavailable.",
      "Generate changes through the existing canonical overlay command path so source preservation, Undo/Redo, recovery and reviewed Save remain shared. Keep upstream board and SoC files unchanged.",
      "Add synthetic regression cases, then verify the selected board with GPIO/UART/I2C/SPI edits, Undo/Redo, missing-overlay Save, reopen and recovery. Record Configure/build separately from physical hardware tests.",
      "Run repository checks and browser/package checks for UI changes. Update this informational catalogue and its evidence handoff only for the coverage actually verified.",
    ],
    paths: [
      {path: "docs/device-tree-editor-support-contributing.md", description: "Board and brand support contribution guide."},
      {path: "src/zephyr_utilities/features/devicetree/chip_catalog.py", description: "Source-derived chip identity and pin inventory."},
      {path: "src/zephyr_utilities/features/devicetree/chip_activation.py", description: "Family dispatch and canonical activation commands."},
      {path: "src/zephyr_utilities/features/devicetree/chip_stm32.py", description: "STM32 package-table route adapter."},
      {path: "src/zephyr_utilities/features/devicetree/chip_nordic.py", description: "Nordic GPIO/PSEL route adapter."},
      {path: "src/zephyr_utilities/features/devicetree/chip_nrf54l.py", description: "nRF54L native-domain and dedicated-pin rules."},
      {path: "tests/test_devicetree_chip_activation.py", description: "Shared activation, source and history regression tests."},
      {path: "src/zephyr_utilities/features/devicetree/web/support-data.js", description: "This version's informational support evidence, never an activation gate."},
    ],
  },
};

export function filterSupportedBoards(query = "", vendor = "all") {
  const terms = String(query ?? "").toLowerCase().trim().split(/\s+/).filter(Boolean);
  const selectedVendor = String(vendor ?? "all").toLowerCase();
  return supportCatalog.verifiedBoards.filter((entry) => {
    if (selectedVendor !== "all" && entry.vendor.toLowerCase() !== selectedVendor) return false;
    const searchable = [entry.name, entry.vendor, entry.soc, entry.target, ...entry.features, ...entry.notes].join(" ").toLowerCase();
    return terms.every((term) => searchable.includes(term));
  });
}
