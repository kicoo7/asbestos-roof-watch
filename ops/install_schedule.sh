#!/bin/sh
# Install the weekly launchd job (Mondays 07:00). Remove with: launchctl bootout gui/$(id -u)/com.roofwatch.weekly
set -e
cp "$(dirname "$0")/com.roofwatch.weekly.plist" ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.roofwatch.weekly.plist
launchctl print gui/$(id -u)/com.roofwatch.weekly | head -5
