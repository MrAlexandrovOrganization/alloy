.PHONY: ci-deploy deploy
ci-deploy:
	python3 scripts/ci-deploy.py

deploy:
	python3 scripts/deploy-ready.py
	$(MAKE) recreate
	$(MAKE) smoke

.PHONY: install-deploy-receiver
install-deploy-receiver:
	python3 scripts/install-deploy-receiver.py
