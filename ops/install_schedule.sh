#!/bin/sh
# Install the weekly launchd job (Mondays 07:00). Remove with: launchctl bootout gui/$(id -u)/com.roofwatch.weekly
# The job runs run_weekly.py with the repository's own .venv, wherever the repository is checked out.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
sed "s|__REPO__|$REPO|g" "$REPO/ops/com.roofwatch.weekly.plist" > ~/Library/LaunchAgents/com.roofwatch.weekly.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.roofwatch.weekly.plist
launchctl print gui/$(id -u)/com.roofwatch.weekly | head -5
