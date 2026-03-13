SHELL := /bin/bash
.DEFAULT_GOAL := help

REPO_ROOT  := $(shell pwd)
STACK_NAME ?= muteq-dashboard
DOMAIN     ?= www.hoongram.com

# ── Helpers ──────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Code quality ──────────────────────────────────────────────────────────────
.PHONY: fmt
fmt: ## Format all Python code with ruff
	ruff format .
	ruff check --fix --select I .

.PHONY: lint
lint: ## Lint all Python code with ruff (no auto-fix)
	ruff check .

.PHONY: fmt-check
fmt-check: ## Check formatting without making changes (CI-safe)
	ruff format --check .
	ruff check --select I .

# ── Clean ─────────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove Python caches and build artefacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	find . -type d -name '*.egg-info' -exec rm -rf {} +
	find . -type d -name '.ruff_cache' -exec rm -rf {} +
	find . -type d -name '.pytest_cache' -exec rm -rf {} +
	@echo "✅ Clean."

# ── AWS ───────────────────────────────────────────────────────────────────────
# Deploy the S3/CloudFront/ACM stack to us-east-1 (required for CloudFront certs).
# Requires: HostedZoneId — Route53 hosted zone ID for hoongram.com
#
#   make aws-deploy HostedZoneId=ZXXXXXXXXXXXXX

.PHONY: aws-deploy
aws-deploy: ## Deploy (or update) the S3/CloudFront CloudFormation stack
	@: $${HostedZoneId:?'HostedZoneId is required: make aws-deploy HostedZoneId=ZXXX'}
	aws cloudformation deploy \
	  --stack-name $(STACK_NAME) \
	  --template-file cloudformation.yml \
	  --region us-east-1 \
	  --parameter-overrides \
	    DomainName=$(DOMAIN) \
	    HostedZoneId=$(HostedZoneId)
	@echo ""
	@echo "✅ Stack deployed. Outputs:"
	@aws cloudformation describe-stacks \
	  --stack-name $(STACK_NAME) \
	  --region us-east-1 \
	  --query 'Stacks[0].Outputs' \
	  --output table

.PHONY: aws-status
aws-status: ## Show current CloudFormation stack status and outputs
	@aws cloudformation describe-stacks \
	  --stack-name $(STACK_NAME) \
	  --region us-east-1 \
	  --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}' \
	  --output table

.PHONY: aws-delete
aws-delete: ## Tear down the CloudFormation stack (prompts for confirmation)
	@read -p "Delete stack '$(STACK_NAME)'? This is irreversible. [y/N] " confirm; \
	  [[ "$$confirm" == "y" || "$$confirm" == "Y" ]] || (echo "Aborted."; exit 1)
	aws cloudformation delete-stack --stack-name $(STACK_NAME) --region us-east-1
	@echo "Waiting for deletion..."
	aws cloudformation wait stack-delete-complete --stack-name $(STACK_NAME) --region us-east-1
	@echo "✅ Stack deleted."
