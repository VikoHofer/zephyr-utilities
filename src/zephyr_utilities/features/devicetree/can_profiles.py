"""Reviewed CAN identities; property names alone do not establish CAN hardware."""


CAN_CONTROLLER_PROFILES = (
    {"id": "zephyr-can-loopback", "compatible": "zephyr,can-loopback", "fd": True,
     "description": "Zephyr emulated CAN loopback controller",
     "evidence": ("dts/bindings/can/zephyr,can-loopback.yaml", "drivers/can/can_loopback.c")},
    {"id": "microchip-mcp2515", "compatible": "microchip,mcp2515", "fd": False,
     "description": "Microchip MCP2515 SPI CAN controller",
     "evidence": ("dts/bindings/can/microchip,mcp2515.yaml", "drivers/can/can_mcp2515.c")},
    {"id": "nxp-flexcan", "compatible": "nxp,flexcan", "fd": False,
     "description": "NXP FlexCAN controller",
     "evidence": ("dts/bindings/can/nxp,flexcan.yaml", "drivers/can/can_mcux_flexcan.c")},
)

CAN_TRANSCEIVER_PROFILES = (
    {"id": "can-transceiver-gpio", "compatible": "can-transceiver-gpio",
     "description": "CAN transceiver controlled by enable or standby GPIOs",
     "evidence": ("dts/bindings/phy/can-transceiver-gpio.yaml", "dts/bindings/phy/can-transceiver.yaml",
                  "drivers/can/transceiver/can_transceiver_gpio.c", "include/zephyr/drivers/gpio.h")},
)

# CAN_DT_DRIVER_CONFIG_GET chooses modern bitrate properties before deprecated aliases.
# can_calc_timing accepts nominal rates up to 1 Mbit/s and sample points below 1000.
# DT_CAN_TRANSCEIVER_*_BITRATE prefers phys over the exact can-transceiver child.
CAN_COMMON_EVIDENCE = ("dts/bindings/can/can-controller.yaml", "dts/bindings/can/can-fd-controller.yaml",
                       "include/zephyr/drivers/can.h", "include/zephyr/devicetree/can.h", "drivers/can/can_common.c")
