# Aloud — push-to-talk dictation for macOS
PYTHON ?= ./.venv/bin/python
export PYTHONPATH := src

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: everything
everything: ## One command: dependencies, model, build, install
	./scripts/install.sh

.PHONY: bootstrap
bootstrap: ## Install toolchain, dependencies, whisper.cpp, and a model
	./scripts/bootstrap.sh

.PHONY: run
run: ## Run from the terminal (permissions attach to the terminal app)
	$(PYTHON) -m aloud run

.PHONY: doctor
doctor: ## Report engine, input device, and permission status
	$(PYTHON) -m aloud doctor

.PHONY: warm
warm: ## Load the selected engine, downloading its model if needed
	$(PYTHON) -m aloud warm

.PHONY: tokens
tokens: ## Dump the design tokens to docs/tokens.json
	$(PYTHON) -c "from aloud.ui import tokens; print(tokens.as_json())" > docs/tokens.json
	@echo "wrote docs/tokens.json"

.PHONY: icon
icon: ## Regenerate the app icon from the procedural source
	python3 scripts/make_icon_png.py --out assets/Aloud-1024.png --size 1024
	./scripts/make_icns.sh

.PHONY: dev-app
dev-app: ## Build the .app into dist/ and launch it
	./scripts/build_app.sh alias
	open dist/Aloud.app

.PHONY: app
app: ## Build Aloud.app into dist/
	./scripts/build_app.sh alias

.PHONY: app-standalone
app-standalone: ## Build a self-contained .app (experimental — see build_app.sh)
	./scripts/build_app.sh standalone

.PHONY: diagnose
diagnose: ## Run the installed app's binary directly to see the real error
	@echo "==> /Applications/Aloud.app/Contents/MacOS/Aloud"
	@echo "    A py2app 'Launch error' dialog is a traceback that got swallowed."
	@echo "    This prints it. Ctrl-C to stop once the app is running."
	@echo
	@/Applications/Aloud.app/Contents/MacOS/Aloud || true

.PHONY: install
install: app ## Build and move Aloud.app into /Applications
	@if [ -d /Applications/Aloud.app ]; then \
		echo "==> Replacing the existing /Applications/Aloud.app"; \
		rm -rf /Applications/Aloud.app; \
	fi
	cp -R dist/Aloud.app /Applications/
	@echo "==> Installed /Applications/Aloud.app"
	@echo "    Open it from Launchpad or Spotlight, then grant Accessibility access."

.PHONY: test
test: ## Run the unit tests
	$(PYTHON) -m pytest -q

.PHONY: clean
clean: ## Remove build output
	rm -rf build dist assets/Aloud.iconset assets/Aloud.icns
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
