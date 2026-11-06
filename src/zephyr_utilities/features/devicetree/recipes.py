"""Built-in, data-only module recipes; selected local bindings remain authoritative."""

from copy import deepcopy


CATALOGUE_VERSION = 1
_RECIPES = (
    {"id": "bme280-i2c", "version": 1, "name": "BME280 environmental sensor (I2C)",
     "description": "One Bosch BME280 sensor on an explicitly selected local I2C bus.",
     "kind": "i2c", "compatible": "bosch,bme280", "node_name": "sensor",
     "software_requirements": ["Sensor API and BME280 driver (CONFIG_SENSOR, CONFIG_BME280).",
                               "I2C support and an enabled, configured controller are required."]},
    {"id": "bme280-spi", "version": 1, "name": "BME280 environmental sensor (SPI)",
     "description": "One Bosch BME280 sensor with an explicit chip-select index and SPI frequency.",
     "kind": "spi", "compatible": "bosch,bme280", "node_name": "sensor",
     "software_requirements": ["Sensor API and BME280 driver (CONFIG_SENSOR, CONFIG_BME280).",
                               "SPI support and an enabled, configured controller are required."]},
    {"id": "gpio-led", "version": 1, "name": "GPIO indicator",
     "description": "One LED in an existing gpio-leds group, using explicit provider cells.",
     "kind": "led", "compatible": None, "node_name": "led",
     "software_requirements": ["GPIO support and application code are required.",
                               "Using the LED API additionally needs CONFIG_LED and CONFIG_LED_GPIO."]},
    {"id": "gpio-button", "version": 1, "name": "GPIO input button",
     "description": "One button in an existing gpio-keys group, with an explicit input event code.",
     "kind": "button", "compatible": None, "node_name": "button",
     "software_requirements": ["GPIO support and the input subsystem (CONFIG_INPUT, CONFIG_INPUT_GPIO_KEYS).",
                               "The application must handle the selected input event code."]},
)


def recipes():
    """Return detached data; recipes cannot execute tools or modify configuration."""
    return deepcopy(list(_RECIPES))
