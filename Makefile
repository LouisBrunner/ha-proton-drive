DC_SHELL ?= ash

all:
.PHONY: all

.venv:
	uv venv

setup: .venv
	uv sync --group local
.PHONY: setup

docker-setup:
	uv sync --group docker
.PHONY: docker-setup

deploy-local:
	scp -rP 2020 custom_components/proton_drive root@homeassistant.local:/homeassistant/custom_components/
.PHONY: deploy-local

devcontainer-start:
	docker compose up --build
.PHONY: devcontainer-start

devcontainer:
	docker compose exec -it devcontainer $(DC_SHELL)
.PHONY: devcontainer

docker-dev:
	@if [ ! -d "$(PWD)/config" ]; then \
		mkdir -p "$(PWD)/config"; \
		uv run hass --config "$(PWD)/config" --script ensure_config; \
	fi
	PYTHONPATH="$(PYTHONPATH):$(PWD)/custom_components" uv run hass --config "$(PWD)/config" --debug
.PHONY: docker-dev

vet:
	uv run ruff check
	uv run ruff format --diff
	uv run ty check
.PHONY: vet

format-fix:
	uv run ruff format
	uv run ruff check --fix
.PHONY: format-fix

manifest-sync:
	uv run scripts/manifest_sync.py
.PHONY: manifest-sync
