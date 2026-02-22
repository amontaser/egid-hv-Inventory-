#!/bin/bash
# Restart Gunicorn with the new configuration

set -e

PROJECT_DIR="/opt/hyperv_inventory"
VENV_PATH="$PROJECT_DIR/venv"
PID_FILE="$PROJECT_DIR/pids/gunicorn.pid"
LOG_FILE="$PROJECT_DIR/logs/gunicorn.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "======================================"
echo "  Gunicorn Restart Script"
echo "======================================"
echo ""

# Kill existing Gunicorn processes
echo -e "${YELLOW}Stopping existing Gunicorn workers...${NC}"
pkill -f "gunicorn.*wsgi:app" || true
sleep 2

# Force kill if still running
if pgrep -f "gunicorn.*wsgi:app" > /dev/null; then
    echo -e "${RED}Force killing remaining processes...${NC}"
    pkill -9 -f "gunicorn.*wsgi:app" || true
    sleep 1
fi

# Clean up PID file
rm -f "$PID_FILE"

# Create log directory
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$PID_FILE")"

# Start Gunicorn with new configuration
echo -e "${GREEN}Starting Gunicorn with configuration file...${NC}"
echo "Config: $PROJECT_DIR/gunicorn.conf.py"
echo "Log: $LOG_FILE"
echo ""

cd "$PROJECT_DIR"
source "$VENV_PATH/bin/activate"

# Start in foreground or background
if [ "$1" == "--daemon" ]; then
    echo "Starting in daemon mode..."
    nohup gunicorn --config gunicorn.conf.py wsgi:app >> "$LOG_FILE" 2>&1 &
    echo -e "${GREEN}✓ Gunicorn started in background${NC}"
    sleep 2
    echo ""
    echo "Check status: ps aux | grep gunicorn"
    echo "View logs: tail -f $LOG_FILE"
else
    echo -e "${GREEN}Starting in foreground (Ctrl+C to stop)...${NC}"
    echo ""
    gunicorn --config gunicorn.conf.py wsgi:app
fi
