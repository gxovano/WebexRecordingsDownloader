#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CSV_PATH="${RECORDINGS_CSV:-../recordings.csv}"
DOWNLOAD_DIRS="${DOWNLOAD_DIRS:-/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings}"
WORKERS_PER_DISK="${WORKERS_PER_DISK:-1}"
STATE_FILE="${DOWNLOAD_STATE:-$SCRIPT_DIR/download_state.json}"
MAX_WORKER_RESTARTS="${MAX_WORKER_RESTARTS:-5}"
RESTART_DELAY_SEC="${RESTART_DELAY_SEC:-15}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-5}"
MEM_RESERVE_MB="${MEM_RESERVE_MB:-2048}"
MEM_PER_WORKER_MB="${MEM_PER_WORKER_MB:-400}"
AUTO_WORKERS="${AUTO_WORKERS:-0}"

PYTHON="${PYTHON:-python3}"

IFS=',' read -r -a DISKS <<< "$DOWNLOAD_DIRS"
DISK_COUNT="${#DISKS[@]}"

declare -A WORKER_PID
declare -A WORKER_RESTART_COUNT
declare -a ACTIVE_WORKERS=()

read_mem_available_mb() {
  local kb
  kb=$(awk '/^MemAvailable:/ { print $2 }' /proc/meminfo)
  echo $((kb / 1024))
}

read_mem_total_mb() {
  local kb
  kb=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)
  echo $((kb / 1024))
}

estimate_max_workers() {
  local mem_available_mb=$1
  local usable=$((mem_available_mb - MEM_RESERVE_MB))
  if (( usable < MEM_PER_WORKER_MB )); then
    echo 1
    return
  fi
  echo $((usable / MEM_PER_WORKER_MB))
}

configure_workers_from_memory() {
  local mem_total_mb mem_available_mb max_workers suggested_per_disk total_workers

  mem_total_mb=$(read_mem_total_mb)
  mem_available_mb=$(read_mem_available_mb)
  max_workers=$(estimate_max_workers "$mem_available_mb")
  suggested_per_disk=$((max_workers / DISK_COUNT))
  if (( suggested_per_disk < 1 )); then
    suggested_per_disk=1
  fi

  echo "Memoria: ${mem_total_mb} MB total, ${mem_available_mb} MB disponivel"
  echo "Estimativa: ~${MEM_PER_WORKER_MB} MB por worker (download em streaming), reserva ${MEM_RESERVE_MB} MB para o sistema"
  echo "Workers seguros agora: ate ${max_workers} total (~${suggested_per_disk} por disco)"

  if [[ "$AUTO_WORKERS" == "1" ]]; then
    WORKERS_PER_DISK=$suggested_per_disk
    echo "AUTO_WORKERS=1: usando WORKERS_PER_DISK=${WORKERS_PER_DISK}"
  fi

  total_workers=$((DISK_COUNT * WORKERS_PER_DISK))
  if (( total_workers > max_workers )); then
    echo ""
    echo "AVISO: ${total_workers} workers configurados, mas a RAM disponivel suporta ~${max_workers}."
    echo "       Risco de OOM. Sugestao: WORKERS_PER_DISK=${suggested_per_disk} ou AUTO_WORKERS=1"
    echo ""
  fi
}

is_killed_exit_code() {
  local code=$1
  (( code == 137 || code == 143 || code == 9 ))
}

start_worker() {
  local disk=$1
  local worker=$2
  local key="${disk}-${worker}"
  local log_file="download-disk${disk}-worker${worker}.log"

  "$PYTHON" download_recordings.py \
    --csv "$CSV_PATH" \
    --dirs "$DOWNLOAD_DIRS" \
    --disk-index "$disk" \
    --worker-index "$worker" \
    --workers-per-disk "$WORKERS_PER_DISK" \
    --state-file "$STATE_FILE" \
    >> "$log_file" 2>&1 &

  WORKER_PID["$key"]=$!
  echo "Worker disco $disk / worker $worker iniciado (PID ${WORKER_PID[$key]}, log: $log_file)"
}

reap_worker() {
  local key=$1
  local disk=${key%-*}
  local worker=${key#*-}
  local pid=${WORKER_PID[$key]}
  local exit_code=0

  wait "$pid" || exit_code=$?

  if (( exit_code == 0 )); then
    echo "Worker disco $disk / worker $worker concluiu."
    return 0
  fi

  if is_killed_exit_code "$exit_code"; then
    local restarts=${WORKER_RESTART_COUNT[$key]:-0}
    echo "Worker disco $disk / worker $worker foi morto (codigo $exit_code). Provavel falta de memoria (OOM)."
    if (( restarts < MAX_WORKER_RESTARTS )); then
      WORKER_RESTART_COUNT[$key]=$((restarts + 1))
      echo "Reiniciando em ${RESTART_DELAY_SEC}s (tentativa $((restarts + 1))/${MAX_WORKER_RESTARTS})..."
      sleep "$RESTART_DELAY_SEC"
      start_worker "$disk" "$worker"
      return 1
    fi
    echo "Worker disco $disk / worker $worker nao sera reiniciado (limite de ${MAX_WORKER_RESTARTS} tentativas)."
    return 0
  fi

  echo "Worker disco $disk / worker $worker falhou com codigo $exit_code."
  return 0
}

echo "CSV: $CSV_PATH"
echo "Discos: $DOWNLOAD_DIRS"
configure_workers_from_memory
echo "Workers por disco: $WORKERS_PER_DISK"
echo "Estado: $STATE_FILE"
echo "Reinicios por worker (OOM): ate $MAX_WORKER_RESTARTS"

"$PYTHON" download_recordings.py \
  --csv "$CSV_PATH" \
  --dirs "$DOWNLOAD_DIRS" \
  --workers-per-disk "$WORKERS_PER_DISK" \
  --state-file "$STATE_FILE" \
  --show-distribution

"$PYTHON" download_recordings.py \
  --csv "$CSV_PATH" \
  --dirs "$DOWNLOAD_DIRS" \
  --state-file "$STATE_FILE" \
  --show-status

for ((disk=0; disk<DISK_COUNT; disk++)); do
  for ((worker=0; worker<WORKERS_PER_DISK; worker++)); do
    start_worker "$disk" "$worker"
    ACTIVE_WORKERS+=("${disk}-${worker}")
  done
done

while ((${#ACTIVE_WORKERS[@]} > 0)); do
  STILL_RUNNING=()
  for key in "${ACTIVE_WORKERS[@]}"; do
    pid=${WORKER_PID[$key]}
    if kill -0 "$pid" 2>/dev/null; then
      STILL_RUNNING+=("$key")
      continue
    fi
    if reap_worker "$key"; then
      continue
    fi
    STILL_RUNNING+=("$key")
  done
  ACTIVE_WORKERS=("${STILL_RUNNING[@]}")
  if ((${#ACTIVE_WORKERS[@]} > 0)); then
    sleep "$POLL_INTERVAL_SEC"
  fi
done

echo "Todos os workers finalizaram."

"$PYTHON" download_recordings.py \
  --csv "$CSV_PATH" \
  --dirs "$DOWNLOAD_DIRS" \
  --state-file "$STATE_FILE" \
  --show-status
