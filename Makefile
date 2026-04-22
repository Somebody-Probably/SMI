.PHONY: install test doctor strict-doctor manual build clean-build

PYTHON ?= python3
VENV ?= .venv

install:
	$(PYTHON) -m venv $(VENV)
	. $(VENV)/bin/activate && python -m pip install --upgrade pip
	. $(VENV)/bin/activate && python -m pip install -e .

test:
	. $(VENV)/bin/activate && python -m pytest

doctor:
	. $(VENV)/bin/activate && research-smi-doctor

strict-doctor:
	. $(VENV)/bin/activate && research-smi-doctor --strict

manual:
	@sed -n '1,260p' MANUAL.md

build: clean-build
	. $(VENV)/bin/activate && python -m pip install --upgrade build
	. $(VENV)/bin/activate && python -m build

clean-build:
	rm -rf build dist *.egg-info src/*.egg-info
