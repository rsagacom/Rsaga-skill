#!/usr/bin/env bash
set -euo pipefail

# Domestic ModelScope queue for the RTX 3060 H3 complete-model matrix.
# This script is copied to the Linux host and run there; it never touches
# WeChat Bot/Qwen processes and never removes an existing partial file.

LOG_DIR="/mnt/gaosu_sata/ComfyUI/models/h3-domestic-download-logs"
mkdir -p "$LOG_DIR"

verify_and_promote() {
    local label="$1" partial="$2" final="$3" expected_size="$4" expected_sha="$5"
    if [[ -f "$final" ]]; then
        [[ "$(stat -c %s "$final")" -eq "$expected_size" ]] || return 1
        local final_checksum="${final}.sha256"
        printf '%s  %s\n' "$expected_sha" "$final" >"$final_checksum"
        sha256sum -c "$final_checksum"
        echo "already_verified $label $final"
        return 0
    fi
    [[ -f "$partial" ]] || return 1
    [[ "$(stat -c %s "$partial")" -eq "$expected_size" ]] || return 1
    local partial_checksum="${partial}.sha256"
    printf '%s  %s\n' "$expected_sha" "$partial" >"$partial_checksum"
    sha256sum -c "$partial_checksum"
    mv "$partial" "$final"
    # Rewrite the promoted checksum path; otherwise the final .sha256 file
    # still points at the removed *.domestic.partial filename.
    printf '%s  %s\n' "$expected_sha" "$final" >"${final}.sha256"
    rm -f "$partial_checksum"
    echo "verified_promoted $label $final"
}

download_one() {
    local label="$1" url="$2" final="$3" expected_size="$4" expected_sha="$5"
    local partial="${final}.domestic.partial"
    local log="$LOG_DIR/${label}.log"
    if verify_and_promote "$label" "$partial" "$final" "$expected_size" "$expected_sha"; then
        return 0
    fi
    echo "download_start $label $(date -Is)" | tee -a "$log"
    curl --noproxy '*' -L --fail --retry 20 --retry-all-errors --retry-delay 5 \
        --connect-timeout 30 --max-time 0 --progress-bar -C - -o "$partial" "$url" >>"$log" 2>&1
    [[ "$(stat -c %s "$partial")" -eq "$expected_size" ]]
    local partial_checksum="${partial}.sha256"
    printf '%s  %s\n' "$expected_sha" "$partial" >"$partial_checksum"
    sha256sum -c "$partial_checksum" | tee -a "$log"
    mv "$partial" "$final"
    # Rewrite the promoted checksum path; otherwise the final .sha256 file
    # still points at the removed *.domestic.partial filename.
    printf '%s  %s\n' "$expected_sha" "$final" >"${final}.sha256"
    rm -f "$partial_checksum"
    echo "download_verified $label $(date -Is) $final" | tee -a "$log"
}

# The current FL2VA Q4 download was started separately. Wait for it before
# starting more large transfers so timing and disk I/O remain interpretable.
while pgrep -f 'curl .*MiniMax-H3-FL2VA-Q4_K_M.gguf.single-connection.partial' >/dev/null 2>&1; do
    sleep 30
done

verify_and_promote \
    "fl2va_q4_k_m" \
    "/mnt/gaosu_sata/ComfyUI/models/diffusion_models/MiniMax-H3-FL2VA-Q4_K_M.gguf.single-connection.partial" \
    "/mnt/gaosu_sata/ComfyUI/models/diffusion_models/MiniMax-H3-FL2VA-Q4_K_M.gguf" \
    19864208160 \
    5e8fa6e960d5fbd547390ceec63fcead275435d8f3bd2466a8a2cbd8c2e361e3

download_one \
    "ref2va_q4_k_m" \
    "https://modelscope.cn/models/realrebelai/MiniMax-H3_GGUFs/resolve/master/MiniMax-H3-REF2VA-Q4_K_M.gguf" \
    "/mnt/gaosu_sata/ComfyUI/models/diffusion_models/MiniMax-H3-REF2VA-Q4_K_M.gguf" \
    19864208064 \
    17925612821ea3037ffaf5f7f9789f5460e87025385bd45e9ec6c7d536684d56

download_one \
    "qwen_q4_k_m" \
    "https://modelscope.cn/models/realrebelai/MiniMax-H3_GGUFs/resolve/master/qwen3vl-32B-MiniMax-H3-Q4_K_M.gguf" \
    "/mnt/gaosu_sata/ComfyUI/models/text_encoders/qwen3vl-32B-MiniMax-H3-Q4_K_M.gguf" \
    14576977888 \
    1bf75e038c5895b97b6ea16cc1e3d32076254b06ec3df10657650d86dc82279e

download_one \
    "ref2va_int8" \
    "https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors" \
    "/mnt/gaosu_sata/ComfyUI/models/diffusion_models/minimax_h3_ref2va_int8_convrot.safetensors" \
    34038894550 \
    9eef934046a0671bc8a5daf87100705e1478419c574cfde70c50fbe6885f76a9

download_one \
    "ref2va_pruned_int8" \
    "https://modelscope.cn/models/Comfy-Org/MiniMax-H3/resolve/master/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" \
    "/mnt/gaosu_sata/ComfyUI/models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" \
    20970379616 \
    9255f52b6677845ad238f20dfaafa94727053694127ab7f255c048f0f9365779

echo "h3_domestic_download_queue_complete $(date -Is)"
