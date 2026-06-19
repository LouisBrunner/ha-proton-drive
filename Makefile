DC_SHELL ?= ash

all:
.PHONY: all

deploy:
	scp -rP 2020 custom_components/proton_drive root@homeassistant.local:/homeassistant/custom_components/
.PHONY: deploy

devcontainer-start:
	docker compose up --build
.PHONY: devcontainer-start

devcontainer:
	docker compose exec -it devcontainer $(DC_SHELL)
.PHONY: devcontainer
