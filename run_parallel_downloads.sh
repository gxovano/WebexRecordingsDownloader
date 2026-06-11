#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CSV_PATH="${RECORDINGS_CSV:-../recordings.csv}"
DOWNLOAD_DIRS="${DOWNLOAD_DIRS:-/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings}"

IFS=',' read -r -a DISKS <<< "$DOWNLOAD_DIRS"
DISK_COUNT="${#DISKS[@]}"

echo "CSV: $CSV_PATH"
echo "Discos: $DOWNLOAD_DIRS"
python download_recordings.py --csv "$CSV_PATH" --dirs "$DOWNLOAD_DIRS" --show-distribution

for ((index=0; index<DISK_COUNT; index++)); do
  python download_recordings.py \
    --csv "$CSV_PATH" \
    --dirs "$DOWNLOAD_DIRS" \
    --disk-index "$index" \
    > "download-disk-${index}.log" 2>&1 &
  echo "Worker do disco $index iniciado (log: download-disk-${index}.log)"
done

wait
echo "Todos os workers finalizaram."
