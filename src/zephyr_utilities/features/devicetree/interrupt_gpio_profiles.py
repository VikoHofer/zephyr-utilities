"""Data-only interrupt GPIO signal profiles; local binding evidence is still required."""


INTERRUPT_GPIO_RECIPES = (
    {"id": "bmi270-int1", "compatible": "bosch,bmi270", "property": "irq-gpios", "signal": "INT1",
     "description": "BMI270 INT1 connection. This form covers one specifier; the optional INT2 connection requires generic binding or source editing. Driver trigger selection, interrupt enablement and software configuration are not established by this connection.",
     "evidence": ("dts/bindings/sensor/bosch,bmi270.yaml", "drivers/sensor/bosch/bmi270/bmi270.c",
                  "drivers/sensor/bosch/bmi270/bmi270_trigger.c")},
    {"id": "hdc2010-drdy-int", "compatible": "ti,hdc2010", "property": "int-gpios", "signal": "DRDY/INT",
     "description": "HDC2010 DRDY/INT connection. The reviewed reference binding describes an active-low open-drain signal; check the selected binding and enter flags appropriate to the actual connection. Pull resistors, interrupt enablement and driver behavior remain unverified.",
     "evidence": ("dts/bindings/sensor/ti,hdc2010.yaml", "dts/bindings/sensor/ti,hdc20xx.yaml",
                  "drivers/sensor/ti/ti_hdc20xx/ti_hdc20xx.c")},
    {"id": "max30210-int1", "compatible": "adi,max30210", "property": "interrupt-gpios", "signal": "INT1",
     "description": "MAX30210 INT1 connection. The reviewed reference binding describes a signal that defaults to active low; check the selected binding and actual connection. Logical polarity in GPIO flags does not choose the driver's interrupt trigger; thresholds, driver configuration and interrupt operation remain unverified.",
     "evidence": ("dts/bindings/sensor/adi,max30210.yaml", "drivers/sensor/adi/max30210/max30210.c",
                  "drivers/sensor/adi/max30210/max30210_trigger.c")},
)
