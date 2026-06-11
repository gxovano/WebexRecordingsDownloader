#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CSV_PATH="${RECORDINGS_CSV:-../recordings.csv}"
DOWNLOAD_DIRS="${DOWNLOAD_DIRS:-/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings}"
WORKERS_PER_DISK="${WORKERS_PER_DISK:-2}"
STATE_FILE="${DOWNLOAD_STATE:-$SCRIPT_DIR/download_state.json}"

IFS=',' read -r -a DISKS <<< "$DOWNLOAD_DIRS"
DISK_COUNT="${#DISKS[@]}"

echo "CSV: $CSV_PATH"
echo "Discos: $DOWNLOAD_DIRS"
echo "Workers por disco: $WORKERS_PER_DISK"
echo "Estado: $STATE_FILE"

python download_recordings.py \
  --csv "$CSV_PATH" \
  --dirs "$DOWNLOAD_DIRS" \
  --workers-per-disk "$WORKERS_PER_DISK" \
  --state-file "$STATE_FILE" \
  --show-distribution

python download_recordings.py \
  --csv "$CSV_PATH" \
  --dirs "$DOWNLOAD_DIRS" \
  --state-file "$STATE_FILE" \
  --show-status

for ((disk=0; disk<DISK_COUNT; disk++)); do
  for ((worker=0; worker<WORKERS_PER_DISK; worker++)); do
    log_file="download-disk${disk}-worker${worker}.log"
    python download_recordings.py \
      --csv "$CSV_PATH" \
      --dirs "$DOWNLOAD_DIRS" \
      --disk-index "$disk" \
      --worker-index "$worker" \
      --workers-per-disk "$WORKERS_PER_DISK" \
      --state-file "$STATE_FILE" \
      > "$log_file" 2>&1 &
    echo "Worker disco $disk / worker $worker iniciado (log: $log_file)"
  done
done

wait
echo "Todos os workers finalizaram."

python download_recordings.py \
  --csv "$CSV_PATH" \
  --dirs "$DOWNLOAD_DIRS" \
  --state-file "$STATE_FILE" \
  --show-status
