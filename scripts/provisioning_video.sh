#!/bin/bash

#set -euo pipefail

### Configuration ###
WORKSPACE_DIR="/opt"
MODELS_DIR="${WORKSPACE_DIR}/ComfyUI/models"
HF_SEMAPHORE_DIR="/workspace/hf_download_sem_$$"
HF_MAX_PARALLEL=3
MODEL_LOG=${MODEL_LOG:-/var/log/provisioning.log}

NODES=(
    "https://github.com/evanspearman/ComfyMath"
    "https://github.com/AlekPet/ComfyUI_Custom_Nodes_AlekPet"
    "https://github.com/pythongosssss/ComfyUI-Custom-Scripts"
    "https://github.com/kijai/ComfyUI-KJNodes"
    "https://github.com/ssitu/ComfyUI_UltimateSDUpscale"
    "https://github.com/cubiq/ComfyUI_essentials"
    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite"
    "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation"
    "https://github.com/chflame163/ComfyUI_LayerStyle"
    "https://github.com/kijai/ComfyUI-MMAudio"
    "https://github.com/MoonHugo/ComfyUI-FFmpeg"
    "https://github.com/kijai/ComfyUI-WanVideoWrapper"
    "https://github.com/christian-byrne/audio-separation-nodes-comfyui"
    "https://github.com/Jonseed/ComfyUI-Detail-Daemon"
    "https://github.com/vrgamegirl19/comfyui-vrgamedevgirl"
    "https://github.com/WASasquatch/was-node-suite-comfyui"
    "https://github.com/Suzie1/ComfyUI_Comfyroll_CustomNodes"
    "https://github.com/yolain/ComfyUI-Easy-Use"
    "https://github.com/kijai/ComfyUI-segment-anything-2"
    "https://github.com/rgthree/rgthree-comfy"
    "https://github.com/Fannovel16/comfyui_controlnet_aux"
    "https://github.com/melMass/comfy_mtb"
    "https://github.com/kijai/ComfyUI-WanAnimatePreprocess.git"
)

# Model declarations: "URL|OUTPUT_PATH"
HF_MODELS=(
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp16.safetensors
  |$MODELS_DIR/text_encoders/umt5_xxl_fp16.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
  |$MODELS_DIR/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_i2v_low_noise_14B_fp16.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_i2v_high_noise_14B_fp16.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_animate_14B_bf16.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_animate_14B_bf16.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_i2v_480p_14B_fp16.safetensors
  |$MODELS_DIR/diffusion_models/wan2.1_i2v_480p_14B_fp16.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_i2v_720p_14B_fp16.safetensors
  |$MODELS_DIR/diffusion_models/wan2.1_i2v_720p_14B_fp16.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
  "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors
  |$MODELS_DIR/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
)

#Use custom models from R2 cloud storage instead of HuggingFace for faster provisioning.
HF_MODELS=()
### End Configuration ###

script_cleanup() {
   rm -rf "$HF_SEMAPHORE_DIR"
}

# If this script fails we cannot let a serverless worker be marked as ready.
script_error() {
    local exit_code=$?
    local line_number=$1
    echo "[ERROR] Provisioning Script failed at line $line_number with exit code $exit_code" | tee -a "$MODEL_LOG"
}

trap script_cleanup EXIT
trap 'script_error $LINENO' ERR

install_custom_nodes() {
    for repo in "${NODES[@]}"; do
        dir="${repo##*/}"
        path="${WORKSPACE_DIR}/ComfyUI/custom_nodes/${dir}"
        requirements="${path}/requirements.txt"
        if [[ ! -d $path ]]; then
            printf "Downloading node: %s...\n" "${repo}"
            git clone "${repo}" "${path}" --recursive
            if [[ -e $requirements ]]; then
                pip install --no-cache-dir -r "${requirements}"
            fi
        fi
    done

    # Remove ComfyUI-Manager
    rm -rf "${WORKSPACE_DIR}/ComfyUI/custom_nodes/ComfyUI-Manager"
}

install_sageattention() {
    pip install --no-cache-dir packaging
    git clone https://github.com/thu-ml/SageAttention.git "${WORKSPACE_DIR}/sageattention"
    cd "${WORKSPACE_DIR}/sageattention"
    export EXT_PARALLEL=4 NVCC_APPEND_FLAGS="--threads 8" MAX_JOBS=32
    python setup.py install
}

main() {
    set_cleanup_job
    mkdir -p "$HF_SEMAPHORE_DIR"
    pids=()

    install_custom_nodes

    # Download all models in parallel
    for model in "${HF_MODELS[@]}"; do
        url="${model%%|*}"
        output_path="${model##*|}"
        download_hf_file "$url" "$output_path" &
        pids+=($!)
    done

    rclone copy -Pv r2:video-models/models/ "$MODELS_DIR/" --s3-chunk-size=100M --transfers=10
    rclone copy -Pv r2:video-models/fonts/ "${WORKSPACE_DIR}/ComfyUI/custom_nodes/ComfyUI-FFmpeg/fonts/"
    rclone copy -Pv r2:video-models/rife47.pth "${WORKSPACE_DIR}/ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation/ckpts/rife/"
    #rclone copy -Pv r2:video-models/models/vae/ "$MODELS_DIR/vae/" --s3-chunk-size=100M --transfers=10
    #rclone copy -Pv r2:video-models/models/loras/ "$MODELS_DIR/loras/" --s3-chunk-size=100M --transfers=10
    #rclone copy -Pv r2:video-models/models/mmaudio/ "$MODELS_DIR/mmaudio/" --s3-chunk-size=100M --transfers=10
    #rclone copy -Pv r2:video-models/models/detection/ "$MODELS_DIR/detection/" --s3-chunk-size=100M --transfers=10
    #rclone copy -Pv r2:video-models/models/clip_vision/ "$MODELS_DIR/clip_vision/" --s3-chunk-size=100M --transfers=10

    # Wait for each job and check exit status
    for pid in "${pids[@]}"; do
        wait "$pid" || exit 1
    done

    # For CUDA 13 support
    pip uninstall onnxruntime-gpu -y
    pip install coloredlogs flatbuffers numpy packaging protobuf sympy
    pip install --pre --index-url https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/ort-cuda-13-nightly/pypi/simple/ onnxruntime-gpu

    #supervisorctl restart comfyui
    # Wait for ComfyUI to start up
    #sleep 30
}

# HuggingFace download helper
download_hf_file() {
  local url="$1"
  local output_path="$2"
  local lockfile="${output_path}.lock"
  local max_retries=5
  local retry_delay=2

  # Acquire slot for parallel download limiting
  local slot=$(acquire_slot)

  # Acquire lock for this specific file
  while ! mkdir "$lockfile" 2>/dev/null; do
    echo "Another process is downloading to $output_path (waiting...)"
    sleep 1
  done

  # Check if file already exists
  if [ -f "$output_path" ]; then
    echo "File already exists: $output_path (skipping)"
    rmdir "$lockfile"
    release_slot "$slot"
    return 0
  fi

  # Extract repo and file path
  local repo=$(echo "$url" | sed -n 's|https://huggingface.co/\([^/]*/[^/]*\)/resolve/.*|\1|p')
  local file_path=$(echo "$url" | sed -n 's|https://huggingface.co/[^/]*/[^/]*/resolve/[^/]*/\(.*\)|\1|p')

  if [ -z "$repo" ] || [ -z "$file_path" ]; then
    echo "ERROR: Invalid HuggingFace URL: $url"
    rmdir "$lockfile"
    release_slot "$slot"
    return 1
  fi

  local temp_dir=$(mktemp -d)
  local attempt=1

  # Retry loop for rate limits and transient failures
  while [ $attempt -le $max_retries ]; do
    echo "Downloading $file_path (attempt $attempt/$max_retries)..."

    if hf download "$repo" \
      "$file_path" \
      --local-dir "$temp_dir" \
      --cache-dir "$temp_dir/.cache" 2>&1; then

      # Success - move file and clean up
      mkdir -p "$(dirname "$output_path")"
      mv "$temp_dir/$file_path" "$output_path"
      rm -rf "$temp_dir"
      rmdir "$lockfile"
      release_slot "$slot"
      echo "✓ Successfully downloaded: $output_path"
      return 0
    else
      echo "✗ Download failed (attempt $attempt/$max_retries), retrying in ${retry_delay}s..."
      sleep $retry_delay
      retry_delay=$((retry_delay * 2))  # Exponential backoff
      attempt=$((attempt + 1))
    fi
  done

  # All retries failed
  echo "ERROR: Failed to download $output_path after $max_retries attempts"
  rm -rf "$temp_dir"
  rmdir "$lockfile"
  release_slot "$slot"
  return 1
}

acquire_slot() {
  while true; do
    local count=$(find "$HF_SEMAPHORE_DIR" -name "slot_*" 2>/dev/null | wc -l)
    if [ $count -lt $HF_MAX_PARALLEL ]; then
      local slot="$HF_SEMAPHORE_DIR/slot_$$_$RANDOM"
      touch "$slot"
      echo "$slot"
      return 0
    fi
    sleep 0.5
  done
}

release_slot() {
  rm -f "$1"
}

# Add a cron job to remove older (oldest +24 hours) output files if disk space is low
set_cleanup_job() {
    if [[ ! -f /opt/proxima-build/bin/clean-output.sh ]]; then
        cat > /opt/proxima-build/bin/clean-output.sh << 'CLEAN_OUTPUT'
#!/bin/bash
output_dir="/opt/ComfyUI/output/"
min_free_mb=512
available_space=$(df -m "${output_dir}" | awk 'NR==2 {print $4}')
if [[ "$available_space" -lt "$min_free_mb" ]]; then
    oldest=$(find "${output_dir}" -mindepth 1 -type f -printf "%T@\n" 2>/dev/null | sort -n | head -1 | awk '{printf "%.0f", $1}')
    if [[ -n "$oldest" ]]; then
        cutoff=$(awk "BEGIN {printf \"%.0f\", ${oldest}+86400}")
        # Only delete files
        find "${output_dir}" -mindepth 1 -type f ! -newermt "@${cutoff}" -delete
        # Delete broken symlinks
        find "${output_dir}" -mindepth 1 -xtype l -delete
        # Now delete *empty* directories separately
        find "${output_dir}" -mindepth 1 -type d -empty -delete
    fi
fi
CLEAN_OUTPUT
        chmod +x /opt/proxima-build/bin/clean-output.sh
    fi

    if ! crontab -l 2>/dev/null | grep -qF 'clean-output.sh'; then
        (crontab -l 2>/dev/null; echo '*/10 * * * * /opt/proxima-build/bin/clean-output.sh') | crontab -
    fi
}

main