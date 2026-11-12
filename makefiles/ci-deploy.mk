.PHONY: ci-deploy deploy
ci-deploy:
	python3 scripts/ci-deploy.py

deploy:
	$(MAKE) observability-receive
	$(MAKE) observability-release-check
	$(MAKE) config-check
	python3 scripts/deploy-ready.py
	$(MAKE) recreate
	$(MAKE) smoke
	$(MAKE) observability-providers-verify

.PHONY: install-deploy-receiver
install-deploy-receiver:
	python3 scripts/install-deploy-receiver.py
