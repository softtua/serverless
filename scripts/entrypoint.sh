#!/bin/bash

export SERVERLESS=true
export BACKEND=comfyui-json
export COMFYUI_API_BASE="http://localhost:18188"
export MODEL_LOG=/var/log/portal/comfyui.log;

# Configure rclone if not already configured
RCLONE_CONF="/root/.config/rclone/rclone.conf"
if [[ ! -f "$RCLONE_CONF" ]]; then
    echo "Creating rclone configuration..."
    mkdir -p "$(dirname "$RCLONE_CONF")"
    cat > "$RCLONE_CONF" << EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = ${S3_ACCESS_KEY_ID}
secret_access_key = ${S3_SECRET_ACCESS_KEY}
endpoint = ${S3_ENDPOINT_URL}
acl = private
EOF
    echo "Rclone configuration created successfully"
fi

if [[ -n $CF_TOKEN ]]; then
  cloudflared service install $CF_TOKEN
fi

if [[ ! -f /opt/comfyui-api-wrapper/proxima ]]; then
    echo "Replacing comfyui-api-wrapper with proxima version"
    rm -rf /opt/comfyui-api-wrapper && \
    git clone https://github.com/softtua/serverless.git /opt/proxima-serverless && \
    mv /opt/proxima-serverless/comfyui-api-wrapper /opt/comfyui-api-wrapper && \
    cd /opt/comfyui-api-wrapper && \
    uv venv
    . .venv/bin/activate
    uv pip install --no-cache-dir -r requirements.txt
    deactivate
    touch /opt/comfyui-api-wrapper/proxima
fi

# We operating only on the ComfyUI provided by the image.
# Volume stored installs are to be managed by the user

[[ -d "${WORKSPACE}/ComfyUI" ]] && exec /opt/instance-tools/bin/entrypoint_base.sh "$@"

# Update ComfyUI
COMFYUI_DIR="/opt/workspace-internal/ComfyUI"

if [[ "${COMFYUI_VERSION:-latest}" = "latest" ]]; then
    tag=$(curl -s https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest 2>/dev/null | jq -r '.tag_name' 2>/dev/null)

    if [[ "$tag" == "null" || -z "$tag" ]]; then
        version="master"
    else
        version="$tag"
    fi
else
    version="$COMFYUI_VERSION"
fi

cd "$COMFYUI_DIR" && \
git fetch --tags && \
git checkout "$version" && \
# Do NOT upgrade existing packages because we will probably break something
uv pip install --python /venv/main/bin/python --no-cache-dir -r requirements.txt

exec /opt/instance-tools/bin/entrypoint_base.sh "$@"