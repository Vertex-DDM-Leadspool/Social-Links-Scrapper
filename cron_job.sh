#!/usr/bin/env bash
# ==============================================================================
# Production Cron Job Runner for LeadsPool Social Scraper & CRM Enricher
# ==============================================================================
#
# Recommended Crontab Entry (runs every 6 hours):
#   0 */6 * * * /bin/bash /path/to/Social-Links-Scrapper/cron_job.sh >> /var/log/social_scraper_cron.log 2>&1
#
# Or run daily at 2:00 AM:
#   0 2 * * * /bin/bash /path/to/Social-Links-Scrapper/cron_job.sh >> /var/log/social_scraper_cron.log 2>&1
# ==============================================================================

set -euo pipefail

# 1. Resolve script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 2. Prevent overlapping cron runs using a lockfile
LOCK_FILE="/tmp/social_scraper.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Another scraper instance is already running. Skipping this cycle."
    exit 0
fi

echo "================================================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Starting Social Scraper Enrichment Cron Job"
echo "Working Directory: $SCRIPT_DIR"
echo "================================================================================"

# 3. Locate Python executable (check virtual environment first)
PYTHON_EXEC="python3"
if [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/venv/bin/python"
elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXEC="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_EXEC="python"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Using Python: $PYTHON_EXEC"

# 4. Load environment variables from .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Loading local .env file"
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
elif [ -f "$SCRIPT_DIR/../.env" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Loading parent .env file"
    set -a
    source "$SCRIPT_DIR/../.env"
    set +a
elif [ -f "$SCRIPT_DIR/../LeadsPool-Outreach/.env" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Loading LeadsPool-Outreach .env file"
    set -a
    source "$SCRIPT_DIR/../LeadsPool-Outreach/.env"
    set +a
fi

# 5. Execute scraper batch
# Configurable via environment variables or CLI flags
BATCH_LIMIT="${LIMIT:-50}"
CONCURRENCY="${CONCURRENCY:-4}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-20}"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] Executing: $PYTHON_EXEC main.py --limit $BATCH_LIMIT --concurrency $CONCURRENCY"

"$PYTHON_EXEC" main.py \
    --limit "$BATCH_LIMIT" \
    --concurrency "$CONCURRENCY" \
    --timeout "$TIMEOUT_SECONDS"

EXIT_CODE=$?

echo "================================================================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SUCCESS] Cron job completed successfully."
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] Cron job failed with exit code $EXIT_CODE."
fi
echo "================================================================================"

exit $EXIT_CODE
