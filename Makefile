.PHONY: dev install

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	@test -f .env || cp .env.example .env

dev:
	.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
