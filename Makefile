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

.PHONY: link
link: ## Install the package into .venv so `aloud` works without PYTHONPATH
	$(PYTHON) -m pip install -e .
	@echo "==> .venv/bin/aloud is now available"

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

.PHONY: report
report: ## Collect everything needed to diagnose a launch failure
	@./scripts/report.sh 2>&1

.PHONY: diagnose
diagnose: ## Run the installed app's binary directly to see the real error
	@echo "==> /Applications/Aloud.app/Contents/MacOS/Aloud"
	@echo "    A Dock double-click that fails silently swallows the traceback."
	@echo "    This prints it. Ctrl-C to stop once the app is running."
	@echo
	@/Applications/Aloud.app/Contents/MacOS/Aloud || true

.PHONY: install
install: ## Build Aloud.app, install it to /Applications, and verify it starts
	./scripts/install_app.sh

.PHONY: tap-test
tap-test: ## Report what the installed app's hotkey tap actually receives
	/Applications/Aloud.app/Contents/MacOS/Aloud tap-test

.PHONY: fix-permissions
fix-permissions: ## Clear a stale Accessibility grant left by an earlier build
	./scripts/fix_permissions.sh

.PHONY: signing-cert
signing-cert: ## Make a stable signing identity so permissions survive rebuilds
	./scripts/make_signing_cert.sh

.PHONY: login-item
login-item: ## Run Aloud at login straight from this folder, without a bundle
	./scripts/login_item.sh install

.PHONY: login-item-remove
login-item-remove: ## Stop running Aloud at login
	./scripts/login_item.sh remove

.PHONY: test
test: ## Run the unit tests
	$(PYTHON) -m pytest -q

.PHONY: clean
clean: ## Remove build output
	rm -rf build dist assets/Aloud.iconset assets/Aloud.icns
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
