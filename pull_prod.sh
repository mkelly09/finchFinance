#!/bin/bash
# Pull production db.sqlite3 and media/ from the server to your local dev machine.
# Run from the project root: bash pull_prod.sh

REMOTE="finch@192.168.1.73"
REMOTE_PATH="/opt/finchfinance"
LOCAL_PATH="$(dirname "$0")"

echo "Pulling db.sqlite3..."
scp "$REMOTE:$REMOTE_PATH/db.sqlite3" "$LOCAL_PATH/db.sqlite3"

echo "Pulling media/..."
scp -r "$REMOTE:$REMOTE_PATH/media/." "$LOCAL_PATH/media/"

echo "Done."
