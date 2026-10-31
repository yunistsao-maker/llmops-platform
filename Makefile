# llmops-platform — back-end components. Every image is built from the repo root (components share libs/).
COMPONENTS := db-migrate llm-gateway crawler-agent publisher view-builder content-api assistant-api request-api \
              key-provisioner notifier dataset-manager test-orchestrator test-runner results-publisher
TOOLS      := mock-llm
TAG        ?= dev
# BUILD_CA=/path/ca.pem  when the build network inspects TLS (passed as a BuildKit secret, never stored)
# PORTAL_WEB_IMAGE=<registry>/llmops/portal-web:<tag>  the page for `make up` (default llmops/portal-web:dev,
#   built from llmops-portal-web: docker build -t llmops/portal-web:dev .)

.PHONY: help build build-% lock test up down logs push render render-check openapi openapi-check smoke
COMPOSE := docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.components.yml

help:
	@echo "make build | build-<component> | lock | test | up | down | logs | smoke | push REGISTRY=... TAG=..."
	@echo "     render | render-check | openapi | openapi-check"

build: $(addprefix build-,$(COMPONENTS) $(TOOLS))

build-%:
	TAG=$(TAG) tools/docker-build.sh $* $(TAG)

lock:  ## re-resolve every requirements.lock with hashes (Python 3.12, linux)
	@for c in $(COMPONENTS); do \
	  if [ -f components/$$c/requirements.in ]; then \
	    echo "lock $$c"; uv pip compile -q components/$$c/requirements.in --generate-hashes \
	      --python-version 3.12 --python-platform linux -o components/$$c/requirements.lock; fi; done

test:  ## unit tests of the shared library and every component (needs TEST_DATABASE_URL for DB tests)
	python -m pytest -q libs/llmops-common/tests
	@for c in $(COMPONENTS); do \
	  if [ -d components/$$c/tests ]; then echo "== $$c"; (cd components/$$c && python -m pytest -q tests) || exit 1; fi; done

up:  ## whole platform with fake models: http://localhost:8080
	@[ -f deploy/compose/.env ] || cp deploy/compose/.env.example deploy/compose/.env
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=50

smoke:  ## end-to-end check through portal-web (make up first)
	python3 tools/smoke.py

render:  ## deploy/components.yaml -> compose components file, handoff table; k8s preview in build/k8s
	python3 tools/render_deploy.py
	python3 tools/render_deploy.py --k8s-out build/k8s --argocd

render-check:
	python3 tools/render_deploy.py --check

openapi:  ## contracts/openapi/*.json from the FastAPI services
	python3 tools/export_openapi.py

openapi-check:
	python3 tools/export_openapi.py --check

push:  ## tag and push every image: make push REGISTRY=<acct>.dkr.ecr.<region>.amazonaws.com
	@test -n "$(REGISTRY)" || (echo "set REGISTRY" && exit 1)
	@for c in $(COMPONENTS) $(TOOLS); do \
	  docker tag llmops/$$c:$(TAG) $(REGISTRY)/llmops/$$c:$(TAG) && docker push $(REGISTRY)/llmops/$$c:$(TAG) || exit 1; done
