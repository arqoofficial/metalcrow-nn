.PHONY: help up up-gpu up-prod up-fast up-no-parser down parser-models \
	build build-gpu build-prod build-no-parser build-parser build-parser-gpu

help:
	@echo "metalcrow dev stack"
	@echo ""
	@echo "  make up            local dev: parser (CPU) + metalcrow"
	@echo "  make up-gpu        same, parser with CUDA"
	@echo "  make up-prod       server: parser (CPU) + metalcrow on :80"
	@echo "  make up-fast       skip model preload check (models must exist)"
	@echo "  make up-no-parser  metalcrow only (parser must be started separately for ingest)"
	@echo "  make down          stop parser + metalcrow"
	@echo "  make parser-models preload Docling/OCR models only"
	@echo ""
	@echo "  make build            rebuild images (parser CPU + metalcrow), no start"
	@echo "  make build-gpu        rebuild with parser CUDA"
	@echo "  make build-prod       rebuild metalcrow prod overlay"
	@echo "  make build-no-parser  rebuild metalcrow only"
	@echo "  make build-parser     rebuild parser only (CPU)"
	@echo "  make build-parser-gpu rebuild parser only (CUDA)"

up:
	@./scripts/dev-up.sh

up-gpu:
	@./scripts/dev-up.sh --gpu

up-prod:
	@./scripts/dev-up.sh --prod

up-fast:
	@./scripts/dev-up.sh --skip-models

up-no-parser:
	@./scripts/dev-up.sh --no-parser

down:
	@./scripts/dev-down.sh

parser-models:
	@./scripts/dev-up.sh --models-only

build:
	@./scripts/dev-build.sh

build-gpu:
	@./scripts/dev-build.sh --gpu

build-prod:
	@./scripts/dev-build.sh --prod

build-no-parser:
	@./scripts/dev-build.sh --no-parser

build-parser:
	@./scripts/dev-build.sh --parser-only

build-parser-gpu:
	@./scripts/dev-build.sh --parser-only --gpu
