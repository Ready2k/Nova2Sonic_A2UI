#!/bin/bash

# Barclays Mortgage Assistant - Service Management Script

# Configuration
PROJECT_ROOT=$(pwd)
SERVER_DIR="$PROJECT_ROOT/server"
CLIENT_DIR="$PROJECT_ROOT/client"
LOG_DIR="$PROJECT_ROOT/logs"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

SERVER_LOG="$LOG_DIR/server.log"
CLIENT_LOG="$LOG_DIR/client.log"

# Ports
SERVER_PORT=8000
CLIENT_PORT=3000

function get_pids() {
    lsof -ti :$1
}

function stop_services() {
    echo "Stopping services..."
    
    SERVER_PIDS=$(get_pids $SERVER_PORT)
    if [ ! -z "$SERVER_PIDS" ]; then
        echo "Killing Server (Port $SERVER_PORT): $SERVER_PIDS"
        kill -9 $SERVER_PIDS 2>/dev/null
    else
        echo "Server not running on port $SERVER_PORT."
    fi

    CLIENT_PIDS=$(get_pids $CLIENT_PORT)
    if [ ! -z "$CLIENT_PIDS" ]; then
        echo "Killing Client (Port $CLIENT_PORT): $CLIENT_PIDS"
        kill -9 $CLIENT_PIDS 2>/dev/null
    else
        echo "Client not running on port $CLIENT_PORT."
    fi
}

function start_services() {
    echo "Starting services..."
    
    # Start Backend
    cd "$SERVER_DIR"
    if [ -f ".venv/bin/python" ]; then
        echo "Starting Server with venv..."
        ./.venv/bin/python -m uvicorn app.main:app --port $SERVER_PORT > "$SERVER_LOG" 2>&1 &
    else
        echo "Starting Server with system python..."
        python3 -m uvicorn app.main:app --port $SERVER_PORT > "$SERVER_LOG" 2>&1 &
    fi
    SERVER_PID=$!
    echo "Server started. Logging to $SERVER_LOG"

    # Start Frontend
    cd "$CLIENT_DIR"
    echo "Starting Client..."
    npm run dev > "$CLIENT_LOG" 2>&1 &
    CLIENT_PID=$!
    echo "Client started. Logging to $CLIENT_LOG"
    
    cd "$PROJECT_ROOT"
    echo "Both services are initializing. Use './manage.sh status' to monitor."
}

function show_status() {
    echo "--- Service Status ---"
    
    SERVER_PIDS=$(get_pids $SERVER_PORT)
    if [ ! -z "$SERVER_PIDS" ]; then
        echo "✅ Server: Running (Port $SERVER_PORT, PIDs: $SERVER_PIDS)"
    else
        echo "❌ Server: Not running"
    fi

    CLIENT_PIDS=$(get_pids $CLIENT_PORT)
    if [ ! -z "$CLIENT_PIDS" ]; then
        echo "✅ Client: Running (Port $CLIENT_PORT, PIDs: $CLIENT_PIDS)"
    else
        echo "❌ Client: Not running"
    fi
}

function show_errors() {
    echo "--- Last errors in Server log ---"
    if [ -f "$SERVER_LOG" ]; then
        grep -iE "ERROR|Exception|Traceback" "$SERVER_LOG" | tail -n 15
    else
        echo "Server log not found."
    fi
    
    echo ""
    echo "--- Last errors in Client log ---"
    if [ -f "$CLIENT_LOG" ]; then
        grep -iE "Error|⨯|Failed" "$CLIENT_LOG" | tail -n 15
    else
        echo "Client log not found."
    fi
}

case "$1" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        stop_services
        sleep 2
        start_services
        ;;
    status)
        show_status
        ;;
    errors)
        show_errors
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|errors}"
        exit 1
        ;;
esac
