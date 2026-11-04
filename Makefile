PYTHON ?= python

.PHONY: run check quick test package setup-dev

run:
	$(PYTHON) ./zephyr-utils

check:
	$(PYTHON) scripts/check.py

quick:
	$(PYTHON) scripts/check.py --quick

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v
	node --test tests/frontend/*.test.mjs

package:
	$(PYTHON) scripts/verify_package.py

setup-dev:
	$(PYTHON) -m pip install -e '.[dev]'
