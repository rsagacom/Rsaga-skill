#!/bin/bash
set -u

ROOT="/Volumes/AJW-Data/Projects/novel-to-comic-engine/.cache/h3-models"
mkdir -p "$ROOT"

download_one() {
  local name="$1"
  local url="$2"
  local target="$3"
  local expected="$4"
  local chunk=$((32 * 1024 * 1024))
  local workers=16
  local parts_dir="${target}.parts"
  local log="${target}.log"
  local count=$(( (expected + chunk - 1) / chunk ))

  mkdir -p "$parts_dir"
  echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] start $name expected=$expected chunks=$count workers=$workers" >> "$log"

  fetch_part() {
    local i="$1"
    local start=$((i * chunk))
    local end=$((start + chunk - 1))
    [ "$end" -ge $((expected - 1)) ] && end=$((expected - 1))
    local want=$((end - start + 1))
    local part="$parts_dir/$(printf '%06d' "$i")"
    local tmp="$part.tmp"
    local got

    if [ -f "$part" ] && [ "$(stat -f %z "$part" 2>/dev/null || echo 0)" -eq "$want" ]; then
      return 0
    fi
    while :; do
      echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] part=$name index=$i range=$start-$end" >> "$log"
      curl --http1.1 -L --fail --silent --show-error \
        --retry 3 --retry-all-errors --connect-timeout 20 --max-time 900 \
        -r "$start-$end" "$url&part=$i&cb=$(date +%s%N)" -o "$tmp" >> "$log" 2>&1 || true
      got="$(stat -f %z "$tmp" 2>/dev/null || echo 0)"
      if [ "$got" -eq "$want" ]; then
        mv -f "$tmp" "$part"
        return 0
      fi
      echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] retry=$name index=$i got=$got want=$want" >> "$log"
      sleep 2
    done
  }
  export -f fetch_part
  export name url target expected chunk parts_dir log

  for worker in $(seq 0 $((workers - 1))); do
    (
      for ((i=worker; i<count; i+=workers)); do
        fetch_part "$i" || exit 1
      done
    ) &
  done
  wait

  local assemble="${target}.assemble"
  : > "$assemble"
  for ((i=0; i<count; i++)); do
    cat "$parts_dir/$(printf '%06d' "$i")" >> "$assemble"
  done
  local total="$(stat -f %z "$assemble")"
  if [ "$total" -ne "$expected" ]; then
    echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] assemble_failed name=$name got=$total want=$expected" >> "$log"
    return 1
  fi
  mv -f "$assemble" "$target"
  echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] complete name=$name size=$total" >> "$log"
}

download_one full_int8 \
  'https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/diffusion_models/minimax_h3_fl2va_int8_convrot.safetensors?download=true' \
  "$ROOT/minimax_h3_fl2va_int8_convrot.safetensors" 34038892334 &

download_one nvfp4 \
  'https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors?download=true' \
  "$ROOT/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" 15687142551 &

download_one t8_lora \
  'https://huggingface.co/t8star/minimax-h3-4step-turbo-loras-comfyui-exp/resolve/main/minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors?download=true' \
  "$ROOT/minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors" 779858903 &

wait
