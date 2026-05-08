#!/bin/bash
# install_cron.sh — Install host crontab entries for Claude Code news analysis.
#
# This runs on the HOST (not inside Docker). It schedules analyze_news.sh
# to run 5 minutes after each container export cycle.
#
# Container schedule (export):    Host schedule (analyze):
#   08:05                           08:10
#   11:05                           11:10
#   14:35                           14:40
#   22:05                           22:10
#   01:05                           01:10
#   04:05                           04:10
set -euo pipefail

NEWS_SCRIPT="/home/onigunsow/trading/scripts/analyze_news.sh"
SCREEN_SCRIPT="/home/onigunsow/trading/scripts/daily_screen.sh"
MARKER="# MoAI: Claude news analysis"

# Ensure the scripts are executable
chmod +x "$NEWS_SCRIPT"
chmod +x "$SCREEN_SCRIPT"

# Build the cron entries
CRON_ENTRIES="
# --- $MARKER ---
# News analysis (6x/day, 5 min after container export)
10 8 * * * $NEWS_SCRIPT
10 11 * * * $NEWS_SCRIPT
40 14 * * * $NEWS_SCRIPT
10 22 * * * $NEWS_SCRIPT
10 1 * * * $NEWS_SCRIPT
10 4 * * * $NEWS_SCRIPT
# Daily screener LLM (Mon-Fri 06:35, after container mechanical filter at 06:30)
35 6 * * 1-5 $SCREEN_SCRIPT
# --- END $MARKER ---
"

# Check if entries already exist
EXISTING=$(crontab -l 2>/dev/null || true)
if echo "$EXISTING" | grep -q "$MARKER"; then
    echo "Cron entries already installed. Replacing..."
    # Remove old entries between markers
    CLEANED=$(echo "$EXISTING" | sed "/# --- $MARKER ---/,/# --- END $MARKER ---/d")
    echo "${CLEANED}${CRON_ENTRIES}" | crontab -
else
    echo "Installing new cron entries..."
    echo "${EXISTING}${CRON_ENTRIES}" | crontab -
fi

echo "Host crontab updated. Current cron jobs:"
crontab -l
echo ""
echo "Done. Container must also be updated to export at :05/:35 and import at :15/:45."
