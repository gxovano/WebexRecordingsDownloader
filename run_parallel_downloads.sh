#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SOURCE_CSV="${RECORDINGS_CSV:-../recordings.csv}"
DOWNLOAD_DIRS="${DOWNLOAD_DIRS:-/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings}"
WORKERS_PER_DISK="${WORKERS_PER_DISK:-1}"
DISK_MIN_FREE_MB="${DISK_MIN_FREE_MB:-5120}"
DISK_SPACE_WAIT_SEC="${DISK_SPACE_WAIT_SEC:-60}"
MAX_WORKER_RESTARTS="${MAX_WORKER_RESTARTS:-5}"
RESTART_DELAY_SEC="${RESTART_DELAY_SEC:-15}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-5}"
MEM_RESERVE_MB="${MEM_RESERVE_MB:-2048}"
MEM_PER_WORKER_MB="${MEM_PER_WORKER_MB:-400}"
AUTO_WORKERS="${AUTO_WORKERS:-0}"
RUN_PREPARE="${RUN_PREPARE:-1}"
RECORDINGS_DATE="${RECORDINGS_DATE:-$(date +%Y%m%d)}"
OUTPUT_DIR="${PREPARE_OUTPUT_DIR:-$SCRIPT_DIR}"

PYTHON="${PYTHON:-python3}"
DOWNLOADER="download_recordings_v2.py"

if grep -q 'buildRecordingFileName' "$SCRIPT_DIR/$DOWNLOADER" 2>/dev/null; then
  echo "ERRO: $DOWNLOADER desatualizado (contem buildRecordingFileName)."
  echo "       Atualize o codigo antes de continuar para evitar nomes legados e duplicatas."
  exit 1
fi

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

csv_has_data_rows() {
  local csv_file=$1
  [[ -f "$csv_file" ]] || return 1
  local lines
  lines=$(wc -l < "$csv_file")
  (( lines > 1 ))
}

disk_csv_path() {
  local disk=$1
  echo "$OUTPUT_DIR/recordings_${RECORDINGS_DATE}_disco${disk}.csv"
}

disk_state_path() {
  local disk=$1
  if [[ -n "${DOWNLOAD_STATE:-}" ]]; then
    printf '%s\n' "${DOWNLOAD_STATE//\{disk\}/$disk}"
    return
  fi
  echo "$SCRIPT_DIR/download_state_disco${disk}.json"
}

start_worker() {
  local disk=$1
  local worker=$2
  local key="${disk}-${worker}"
  local log_file="download-disk${disk}-worker${worker}.log"
  local csv_file
  csv_file=$(disk_csv_path "$disk")

  if ! csv_has_data_rows "$csv_file"; then
    echo "Worker disco $disk / worker $worker ignorado: CSV vazio ou ausente ($csv_file)"
    return 0
  fi

  "$PYTHON" "$DOWNLOADER" \
    --csv "$csv_file" \
    --dirs "$DOWNLOAD_DIRS" \
    --disk-index "$disk" \
    --worker-index "$worker" \
    --workers-per-disk "$WORKERS_PER_DISK" \
    --min-free-mb "$DISK_MIN_FREE_MB" \
    >> "$log_file" 2>&1 &

  WORKER_PID["$key"]=$!
  echo "Worker disco $disk / worker $worker iniciado (PID ${WORKER_PID[$key]}, CSV: $csv_file, log: $log_file)"
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

echo "CSV original: $SOURCE_CSV"
echo "Discos: $DOWNLOAD_DIRS"

if [[ "$RUN_PREPARE" == "1" ]]; then
  echo "Preparando CSVs filtrados (RUN_PREPARE=1)..."
  "$PYTHON" prepare_recordings.py \
    --csv "$SOURCE_CSV" \
    --dirs "$DOWNLOAD_DIRS" \
    --output-dir "$OUTPUT_DIR" \
    --date "$RECORDINGS_DATE"
else
  echo "RUN_PREPARE=0: usando CSVs existentes com data $RECORDINGS_DATE"
fi

PENDING_CSV="$OUTPUT_DIR/recordings_${RECORDINGS_DATE}.csv"
echo "CSV pendentes: $PENDING_CSV"

configure_workers_from_memory
echo "Workers por disco: $WORKERS_PER_DISK"
echo "Estado: um arquivo por disco (ex.: download_state_disco0.json)"
if [[ -n "${DOWNLOAD_STATE:-}" ]]; then
  echo "DOWNLOAD_STATE: $DOWNLOAD_STATE"
fi
echo "Espaco minimo livre por disco: ${DISK_MIN_FREE_MB} MB (aguarda ${DISK_SPACE_WAIT_SEC}s se insuficiente)"
echo "Reinicios por worker (OOM): ate $MAX_WORKER_RESTARTS"

if csv_has_data_rows "$PENDING_CSV"; then
  "$PYTHON" "$DOWNLOADER" \
    --csv "$PENDING_CSV" \
    --dirs "$DOWNLOAD_DIRS" \
    --workers-per-disk "$WORKERS_PER_DISK" \
    --min-free-mb "$DISK_MIN_FREE_MB" \
    --show-distribution

  "$PYTHON" "$DOWNLOADER" \
    --csv "$PENDING_CSV" \
    --dirs "$DOWNLOAD_DIRS" \
    --min-free-mb "$DISK_MIN_FREE_MB" \
    --show-status
else
  echo "Nenhuma gravacao pendente. Encerrando sem iniciar workers."
  exit 0
fi

for ((disk=0; disk<DISK_COUNT; disk++)); do
  for ((worker=0; worker<WORKERS_PER_DISK; worker++)); do
    csv_file=$(disk_csv_path "$disk")
    if csv_has_data_rows "$csv_file"; then
      start_worker "$disk" "$worker"
      ACTIVE_WORKERS+=("${disk}-${worker}")
    else
      echo "Disco $disk sem gravacoes pendentes ($csv_file)"
    fi
  done
done

if ((${#ACTIVE_WORKERS[@]} == 0)); then
  echo "Nenhum worker iniciado."
  exit 0
fi

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

"$PYTHON" "$DOWNLOADER" \
  --csv "$PENDING_CSV" \
  --dirs "$DOWNLOAD_DIRS" \
  --min-free-mb "$DISK_MIN_FREE_MB" \
  --show-status

for ((disk=0; disk<DISK_COUNT; disk++)); do
  state_file=$(disk_state_path "$disk")
  if [[ -f "$state_file" ]]; then
    echo "Estado disco $disk: $state_file"
  fi
done
