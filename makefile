# ============================================================
# xcore-doctype : backend (Python, uvicorn + xcore) et Desk (Vue, Vite)
#
#   make install        dépendances Python et Node
#   make dev            backend (:8000) ET frontend (:5173), rechargement automatique
#   make token          jeton d'accès d'xauth (scripts, curl) ; le Desk, lui, se connecte par e-mail et mot de passe (.env)
#   make check          tout vérifier avant de livrer
# ============================================================
SHELL            := /bin/bash
.DEFAULT_GOAL    := help

HOST             ?= 127.0.0.1
PORT             ?= 8000
UI_PORT          ?= 5173
FIELD            ?= access_token
EMAIL            ?=
PASSWORD         ?=
GRANTS           ?=
PG_URL           ?=

UI               := ui
DIST             := src/xcore_doctype/ui/dist

# Charge `.env` (ADMIN_EMAIL, ADMIN_PASSWORD : le compte administrateur qu'xauth crée au premier démarrage) SANS écraser une variable déjà exportée.
define LOAD_ENV
set -a; [ -f .env ] && . ./.env; set +a
endef


# ============================================================
# 📚 Aide
# ============================================================
help: ## Afficher cette aide
	@echo ""
	@echo "  Démarrage :  make install  puis  make dev"
	@echo "    backend   http://$(HOST):$(PORT)/app/xcore-doctype/   (docs : /docs)"
	@echo "    frontend  http://localhost:$(UI_PORT)/               (Vite, relaie /app vers le backend)"
	@echo "    connexion : e-mail et mot de passe de .env (ADMIN_EMAIL, ADMIN_PASSWORD), créés par make env"
	@echo ""
	@grep -E '^[a-zA-Z0-9_./-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables : HOST=$(HOST) PORT=$(PORT) UI_PORT=$(UI_PORT) PG_URL=<url asyncpg>"
	@echo ""


# ============================================================
# 📦 Installation
# ============================================================
install: install-py install-ui ## Installer les dépendances Python (Poetry) et Node (pnpm)

install-py: ## Installer les dépendances Pythont-of-the-Box System Endpoint
	@poetry install

install-ui: $(UI)/node_modules ## Installer les dépendances du Desk

# Hors de `make install` à dessein : c'est une dépendance OPTIONNELLE (process/impression.md §1), et plutoprint
# pèse ~20 Mo (moteur embarqué). Sans elle, l'aperçu HTML marche, la route PDF répond 503 et le test fumigène
# `tests/unit/test_pdf_engine.py` s'ignore avec sa raison. La CI, elle, l'installe toujours.
install-pdf: ## Installer l'extra d'impression PDF (plutoprint + pypdf) et activer son test fumigène
	@poetry install --extras pdf

# Réinstalle seulement quand package.json ou le verrou changent.
$(UI)/node_modules: $(UI)/package.json $(UI)/pnpm-lock.yaml
	@pnpm -C $(UI) install --frozen-lockfile
	@touch $@

env: ## Préparer le développement : .env (compte administrateur) et clés JWT conf/ (s'ils n'existent pas)
	@poetry run python scripts/dev_setup.py


# ============================================================
# 🚀 Lancement
# ============================================================
dev: env $(UI)/node_modules ## Backend + frontend ensemble (Ctrl-C arrête les deux)
	@echo "▶ backend  http://$(HOST):$(PORT)   ▶ frontend  http://localhost:$(UI_PORT)   ▶ connexion : ADMIN_EMAIL / ADMIN_PASSWORD de .env"
	@$(MAKE) -j2 dev-backend dev-frontend

dev-backend: env ## Backend seul : uvicorn + xcore, rechargé quand le Python ou un DocType (YAML) change
	@[ "$(HOST)" = "127.0.0.1" ] || echo "⚠️  HOST=$(HOST) : le compte administrateur de .env (ADMIN_EMAIL, ADMIN_PASSWORD) est joignable depuis le réseau"
	@$(LOAD_ENV); exec poetry run uvicorn main:app --host $(HOST) --port $(PORT) \
		--reload --reload-dir src --reload-dir app --reload-include '*.yaml'

dev-frontend: $(UI)/node_modules ## Frontend seul : Vite avec rechargement à chaud (le backend doit tourner)
	@DESK_API_URL=http://127.0.0.1:$(PORT) DESK_LOGIN_URL=/app/auth/login pnpm -C $(UI) dev --host 127.0.0.1 --port $(UI_PORT) --strictPort

serve: build-ui env ## Desk CONSTRUIT servi par le backend (comme en livraison), sans rechargement
	@echo "▶ Desk  http://$(HOST):$(PORT)/app/xcore-doctype/desk/"
	@$(LOAD_ENV); exec poetry run uvicorn main:app --host $(HOST) --port $(PORT)

member: env ## Créer un utilisateur de développement : EMAIL=… PASSWORD=… [GRANTS='a:b:c …'] (le serveur doit tourner)
	@$(LOAD_ENV); poetry run python scripts/dev_member.py --base http://$(HOST):$(PORT) --email "$(EMAIL)" --password "$(PASSWORD)" --grants "$(GRANTS)"

token: env ## Jeton d'accès d'xauth (FIELD=tenant_id pour l'organisation) : le serveur doit tourner ; seule la valeur va sur stdout
	@$(LOAD_ENV); poetry run python scripts/login.py --base http://$(HOST):$(PORT) --field $(FIELD)


# ============================================================
# 🏗️ Build
# ============================================================
build-ui: $(UI)/node_modules ## Construire le Desk dans src/xcore_doctype/ui/dist (types vérifiés)
	@pnpm -C $(UI) build

build: build-ui ## Construire le wheel avec le Desk embarqué (dist/)
	@poetry build


# ============================================================
# 🧪 Tests
# ============================================================
test: ## Tous les tests Python (SQLite ; PostgreSQL ignoré sans PG_URL), navigateur et livraison compris
	@poetry run pytest -q

test-unit: ## Tests unitaires Python seuls (rapide)
	@poetry run pytest tests/unit -q

test-pg: ## Tous les tests Python aussi sur PostgreSQL (PG_URL=postgresql+asyncpg://user@host:port/base)
	@test -n "$(PG_URL)" || { echo "PG_URL manquant, ex. make test-pg PG_URL=postgresql+asyncpg://postgres@127.0.0.1:54329/xdt"; exit 1; }
	@XCORE_DOCTYPE_TEST_PG_URL='$(PG_URL)' poetry run pytest -q

test-ui: $(UI)/node_modules ## Tests du Desk (vitest)
	@pnpm -C $(UI) test

typecheck-ui: $(UI)/node_modules ## Vérification de types du Desk (vue-tsc)
	@pnpm -C $(UI) typecheck

e2e: build-ui ## Scénario navigateur seul : vrai Chromium contre un vrai hôte (Desk reconstruit d'abord)
	@poetry run pytest tests/e2e -v

install-check: build-ui ## Le wheel dans un venv vierge + une instance xcore neuve (xcli init) : le critère de livraison
	@poetry run pytest tests/delivery -v

check: build-ui test test-ui ## Tout vérifier : types, build, tests Python (e2e et livraison compris) et tests du Desk

expr-vectors: ## Régénérer tests/vectors/expr_asts.json après un changement de la grammaire des conditions
	@poetry run python scripts/gen_expr_asts.py


# ============================================================
# 🧹 Nettoyage
# ============================================================
clean: ## Supprimer __pycache__ et les .pyc
	@find . \( -path ./$(UI)/node_modules -o -path ./.venv \) -prune -o -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	@find . \( -path ./$(UI)/node_modules -o -path ./.venv \) -prune -o -type f \( -name "*.pyc" -o -name "*.pyo" \) -exec rm -f {} + 2>/dev/null; true

clean-ui: ## Supprimer le Desk construit : `make build-ui` le refait
	@rm -rf $(DIST)

clean-all: clean clean-ui ## clean + clean-ui + node_modules (puis `make install`)
	@rm -rf $(UI)/node_modules


.PHONY: help install install-py install-ui env \
        dev dev-backend dev-frontend serve token member \
        build-ui build \
        test test-unit test-pg test-ui typecheck-ui e2e install-check check expr-vectors \
        clean clean-ui clean-all
