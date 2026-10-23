SHELL := /bin/sh

PYTHON ?= python3.13
VENV := venv
VENV_BIN := $(VENV)/bin
VENV_PYTHON := $(VENV_BIN)/python
PIP := $(VENV_PYTHON) -m pip

DOCKER := docker
COMPOSE := $(DOCKER) compose -f docker-compose.yml

# Bind-mounted into the container; holds the SQLite game file.
DB_DIR := db

# LAN IP of this machine, so the /books QR code points somewhere phones can reach.
# macOS: Wi-Fi is usually en0, Ethernet en1. Linux: first address from hostname -I.
HOST_IP ?= $(shell ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $$1}')
export HOST_IP

.DEFAULT_GOAL := help

.PHONY: help
.PHONY: install test dev clean
.PHONY: build run restart stop ps logs
.PHONY: check-docker

#========== Development ==========#

venv:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		$(PYTHON) -m venv $(VENV); \
	else \
		echo "Virtual environment already exists."; \
	fi

install: venv
	$(PIP) install --upgrade -r requirements.txt

# Rules engine, event fold and a full game through the web UI: the testpaths in pyproject.toml.
test: venv
	$(VENV_PYTHON) -m pytest

# Flask dev server with reload, outside Docker. The game goes in ./monopoly.db.
dev: venv
	$(VENV_BIN)/flask --app 'app:create_app()' run --host 0.0.0.0 --port 8080 --debug

clean:
	find . -type d -name '__pycache__' -not -path './$(VENV)/*' -exec rm -rf {} +
	rm -rf ./.pytest_cache

#========== Docker ==========#

build:
	$(COMPOSE) build

# Created here so it belongs to you, not to root (Docker would create it on Linux).
$(DB_DIR):
	mkdir -p $@

run: check-docker $(DB_DIR)
	$(COMPOSE) up --build --detach
	@$(MAKE) ps
	@echo "Banker: http://localhost:8080   Books: http://$(HOST_IP):8080/books"

restart: stop run

stop: check-docker
	$(COMPOSE) down

ps: check-docker
	$(COMPOSE) ps

logs: check-docker
	$(COMPOSE) logs -f $(SERVICE)

check-docker:
	@command -v $(DOCKER) >/dev/null 2>&1 || { \
		echo "ERROR: Docker is not installed."; \
		exit 1; \
	}
	@$(DOCKER) info >/dev/null 2>&1 || { \
		echo "ERROR: Docker daemon is not running or is inaccessible."; \
		exit 1; \
	}


help:
	@echo "Available targets:"
	@echo "  venv           Create the Python virtual environment (PYTHON=$(PYTHON))"
	@echo "  install        Install Python dependencies"
	@echo "  test           Run the tests"
	@echo "  dev            Run the Flask dev server on port 8080 (no Docker)"
	@echo "  clean          Remove generated Python files"
	@echo "  build          Build the Docker image"
	@echo "  run            Start the banker with docker compose (port 8080)"
	@echo "  restart        Restart the banker"
	@echo "  stop           Stop and remove the container (the game data volume stays)"
	@echo "  ps             Show container status"
	@echo "  logs           Follow logs (SERVICE=name for one service)"
