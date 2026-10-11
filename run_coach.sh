#!/usr/bin/env bash
# Runs the daily coach (for cron / launchd). Usage: ./run_coach.sh
cd "$(dirname "$0")"
mkdir -p logs
./.venv/bin/python -m coach run >> "logs/$(date +%Y-%m).log" 2>&1
