#!/bin/bash
# Complete restart script for Hyper-V Inventory Application
# Restarts Redis, Celery workers, Celery beat, and Gunicorn

set -e

PROJECT_DIR="/opt/hyperv_inventory"
VENV_PATH="$PROJECT_DIR/venv"
PID_DIR="$PROJECT_DIR/pids"
LOG_DIR="$PROJECT_DIR/logs"

# PID files
CELERY_WORKER_PID="$PID_DIR/celery-worker.pid"
CELERY_BEAT_PID="$PID_DIR/celery-beat.pid"
GUNICORN_PID="$PID_DIR/gunicorn.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  Hyper-V Inventory - Full Restart${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""

# Function to check if process is running
is_running() {
    if [ -f "$1" ]; then
        pid=$(cat "$1" 2>/dev/null || echo "")
        if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# Function to stop process by PID file
stop_process() {
    local name="$1"
    local pid_file="$2"
    local signal="${3:-TERM}"

    if is_running "$pid_file"; then
        echo -e "${YELLOW}Stopping $name...${NC}"
        pid=$(cat "$pid_file")
        kill -$signal $pid 2>/dev/null || true
        sleep 2

        # Force kill if still running
        if ps -p $pid > /dev/null 2>&1; then
            echo -e "${RED}Force killing $name (PID $pid)...${NC}"
            kill -9 $pid 2>/dev/null || true
            sleep 1
        fi

        rm -f "$pid_file"
        echo -e "${GREEN}✓ $name stopped${NC}"
    else
        echo -e "${YELLOW}$name is not running${NC}"
        rm -f "$pid_file"
    fi
}

# ============================================================================
# STOP SERVICES
# ============================================================================

echo -e "${YELLOW}[1/4] Stopping Gunicorn...${NC}"
stop_process "Gunicorn" "$GUNICORN_PID" TERM
# Also kill any orphaned gunicorn processes
pkill -f "gunicorn.*wsgi:app" || true
sleep 1

echo ""
echo -e "${YELLOW}[2/4] Stopping Celery Beat...${NC}"
stop_process "Celery Beat" "$CELERY_BEAT_PID" TERM
# Kill any orphaned beat processes
pkill -f "celery.*beat" || true
sleep 1

echo ""
echo -e "${YELLOW}[3/4] Stopping Celery Workers...${NC}"
stop_process "Celery Worker" "$CELERY_WORKER_PID" TERM
# Kill any orphaned worker processes
pkill -f "celery.*worker" || true
sleep 1

echo ""
echo -e "${YELLOW}[4/4] Restarting Redis...${NC}"
if pgrep -x redis-server > /dev/null; then
    echo "Restarting Redis..."
    sudo systemctl restart redis-server || sudo service redis-server restart || pkill -HUP redis-server
    echo -e "${GREEN}✓ Redis restarted${NC}"
else
    echo "Starting Redis..."
    sudo systemctl start redis-server || sudo service redis-server start || redis-server --daemonize yes
    echo -e "${GREEN}✓ Redis started${NC}"
fi
sleep 2

# Verify Redis is running
if ! pgrep -x redis-server > /dev/null; then
    echo -e "${RED}✗ Failed to start Redis!${NC}"
    exit 1
fi

# ============================================================================
# START SERVICES
# ============================================================================

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}  Starting Services${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""

# Create necessary directories
mkdir -p "$PID_DIR"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"
source "$VENV_PATH/bin/activate"

# Start Gunicorn
echo -e "${BLUE}[1/3] Starting Gunicorn...${NC}"
nohup gunicorn --config gunicorn.conf.py wsgi:app >> "$LOG_DIR/gunicorn.log" 2>&1 &
GUNICORN_PID=$!
echo $GUNICORN_PID > "$GUNICORN_PID"
sleep 2

if ps -p $GUNICORN_PID > /dev/null; then
    echo -e "${GREEN}✓ Gunicorn started (PID: $GUNICORN_PID)${NC}"
else
    echo -e "${RED}✗ Failed to start Gunicorn!${NC}"
    tail -20 "$LOG_DIR/gunicorn.log"
fi

# Start Celery Worker
echo ""
echo -e "${BLUE}[2/3] Starting Celery Worker...${NC}"
nohup celery -A celeryconfig worker \
    --queues=hyperv,csv \
    --loglevel=INFO \
    --logfile="$LOG_DIR/celery-worker.log" \
    --pidfile="$CELERY_WORKER_PID" \
    --concurrency=4 \
    >> "$LOG_DIR/celery-worker.log" 2>&1 &
sleep 3

if [ -f "$CELERY_WORKER_PID" ]; then
    WORKER_PID=$(cat "$CELERY_WORKER_PID")
    if ps -p $WORKER_PID > /dev/null; then
        echo -e "${GREEN}✓ Celery Worker started (PID: $WORKER_PID)${NC}"
    else
        echo -e "${RED}✗ Failed to start Celery Worker!${NC}"
        tail -20 "$LOG_DIR/celery-worker.log"
    fi
else
    echo -e "${RED}✗ Failed to start Celery Worker (no PID file)!${NC}"
fi

# Start Celery Beat
echo ""
echo -e "${BLUE}[3/3] Starting Celery Beat...${NC}"
nohup celery -A celeryconfig beat \
    --loglevel=INFO \
    --logfile="$LOG_DIR/celery-beat.log" \
    --pidfile="$CELERY_BEAT_PID" \
    --detach \
    >> "$LOG_DIR/celery-beat.log" 2>&1 &
sleep 2

if [ -f "$CELERY_BEAT_PID" ]; then
    BEAT_PID=$(cat "$CELERY_BEAT_PID")
    if ps -p $BEAT_PID > /dev/null; then
        echo -e "${GREEN}✓ Celery Beat started (PID: $BEAT_PID)${NC}"
    else
        echo -e "${RED}✗ Failed to start Celery Beat!${NC}"
        tail -20 "$LOG_DIR/celery-beat.log"
    fi
else
    echo -e "${RED}✗ Failed to start Celery Beat (no PID file)!${NC}"
fi

# ============================================================================
# STATUS SUMMARY
# ============================================================================

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}  Service Status${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""

# Redis
if pgrep -x redis-server > /dev/null; then
    REDIS_PID=$(pgrep -x redis-server)
    echo -e "${GREEN}✓ Redis${NC}           running (PID: $REDIS_PID)"
else
    echo -e "${RED}✗ Redis${NC}           NOT running"
fi

# Gunicorn
if pgrep -f "gunicorn.*wsgi:app" > /dev/null; then
    GUNICORN_COUNT=$(pgrep -f "gunicorn.*wsgi:app" | wc -l)
    echo -e "${GREEN}✓ Gunicorn${NC}        running ($GUNICORN_COUNT workers)"
else
    echo -e "${RED}✗ Gunicorn${NC}        NOT running"
fi

# Celery Worker
if pgrep -f "celery.*worker" > /dev/null; then
    WORKER_COUNT=$(pgrep -f "celery.*worker" | wc -l)
    echo -e "${GREEN}✓ Celery Worker${NC}   running ($WORKER_COUNT processes)"
else
    echo -e "${RED}✗ Celery Worker${NC}   NOT running"
fi

# Celery Beat
if pgrep -f "celery.*beat" > /dev/null; then
    BEAT_PID=$(pgrep -f "celery.*beat" | head -1)
    echo -e "${GREEN}✓ Celery Beat${NC}     running (PID: $BEAT_PID)"
else
    echo -e "${RED}✗ Celery Beat${NC}     NOT running"
fi

echo ""
echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  Useful Commands${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""
echo "View logs:"
echo "  tail -f $LOG_DIR/gunicorn.log"
echo "  tail -f $LOG_DIR/celery-worker.log"
echo "  tail -f $LOG_DIR/celery-beat.log"
echo ""
echo "Check process status:"
echo "  ps aux | grep -E 'redis|celery|gunicorn'"
echo ""
echo "Test application:"
echo "  curl http://localhost:5000"
echo "  celery -A celeryconfig inspect active"
echo ""
echo -e "${GREEN}✓ Restart complete!${NC}"
echo ""