# Simple Makefile for development convenience
.PHONY: run-dev stop-backend stop-frontend logs

run-dev:
	@bash scripts/restart-servers.sh

stop-backend:
	@pkill -f uvicorn || true

stop-frontend:
	@pkill -f "npm run dev" || pkill -f vite || true

logs:
	@echo "Backend log:" && tail -n 200 backend/logs/uvicorn.log || true
	@echo "\nFrontend log:" && tail -n 200 frontend/logs/vite.log || true
