#!/usr/bin/env bash
set -euo pipefail

# Cross-platform compatible (for Unix) restart script for backend and frontend dev servers.
# Usage: ./scripts/restart-servers.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT=8000
FRONTEND_PORT=5173

echo "Stopping processes on ports ${BACKEND_PORT} and ${FRONTEND_PORT} if running..."

stop_if_listening() {
  local port=$1
  pids=$(lsof -ti tcp:"${port}" || true)
  if [ -n "$pids" ]; then
    echo "Killing processes on port ${port}: $pids"
    kill -9 $pids || true
  else
    echo "No process listening on port ${port}"
  fi
}

stop_if_listening ${BACKEND_PORT}
stop_if_listening ${FRONTEND_PORT}

# Ensure logs folders exist
mkdir -p "${PROJECT_ROOT}/backend/logs"
mkdir -p "${PROJECT_ROOT}/frontend/logs"

echo "Starting backend (uvicorn)..."
# Start backend in background
nohup python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port ${BACKEND_PORT} > "${PROJECT_ROOT}/backend/logs/uvicorn.log" 2>&1 &
backend_pid=$!
sleep 1

echo "Starting frontend (Vite)..."
# Start frontend in background
nohup bash -lc "cd ${PROJECT_ROOT}/frontend && npm run dev" > "${PROJECT_ROOT}/frontend/logs/vite.log" 2>&1 &
frontend_pid=$!

# Wait a little and check ports
sleep 3

check_port() {
  local port=$1
  timeout 1 bash -c "</dev/tcp/127.0.0.1/${port}" 2>/dev/null && return 0 || return 1
}

if check_port ${BACKEND_PORT}; then
  echo "Backend is listening on ${BACKEND_PORT} (PID=${backend_pid})"
else
  echo "Warning: Backend not listening yet. Check backend/logs/uvicorn.log"
fi

if check_port ${FRONTEND_PORT}; then
  echo "Frontend is listening on ${FRONTEND_PORT} (PID=${frontend_pid})"
else
  echo "Warning: Frontend not listening yet. Check frontend/logs/vite.log"
fi

echo "Done. To stop servers, kill the PIDs above or run: pkill -f 'uvicorn' ; pkill -f 'vite'"
