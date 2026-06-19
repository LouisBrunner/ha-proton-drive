INSIDE_DOCKER = $(shell stat /.dockerenv 2>&1 >/dev/null && echo 1 || echo 0)
ifeq ($(INSIDE_DOCKER),1)
endif

BIOME = biome
ifeq ($(shell which biome),)
	BIOME = bunx biome
endif

all:
.PHONY: all

setup:
ifeq ($(INSIDE_DOCKER),1)
# FIXME: completely borked if you use uv sync, lovely
	uv export --format requirements.txt --no-hashes --group hass --group docker --no-dev \
	  | python3 -m pip install --only-binary=:all: -r /dev/stdin
else
	uv sync --group hass
endif
.PHONY: setup

deploy-local:
	scp -rP 2020 custom_components/proton_drive root@homeassistant.local:/homeassistant/custom_components/
.PHONY: deploy-local

devcontainer-start:
	docker compose up --build
.PHONY: devcontainer-start

devcontainer:
	docker compose exec -it devcontainer ash
.PHONY: devcontainer

dev:
ifeq ($(INSIDE_DOCKER),1)
	@if [ ! -d "$(PWD)/config" ]; then \
		mkdir -p "$(PWD)/config"; \
		hass --config "$(PWD)/config" --script ensure_config; \
	fi
	PYTHONPATH="$(PYTHONPATH):$(PWD)/custom_components" hass --config "$(PWD)/config" --debug
else
	@echo "Unsupported outside Docker" && false
endif
.PHONY: dev

vet:
	uv run ruff check
	uv run ruff format --diff
	uv run ty check
.PHONY: vet

vet-toml:
	uv run tombi format --check --diff
	uv run tombi lint --error-on-warnings
.PHONY: vet-toml

format-fix:
	uv run ruff format
	uv run ruff check --fix
	uv run tombi format
.PHONY: format-fix

manifest-sync:
	uv run scripts/manifest_sync.py
	$(BIOME) format --write custom_components/proton_drive/manifest.json
.PHONY: manifest-sync
