#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly ENV_FILE="${PROJECT_ROOT}/deploy/.env"
readonly COMPOSE_FILE="${PROJECT_ROOT}/deploy/compose.yaml"
readonly CUDA_IMAGE="nvidia/cuda:12.6.3-runtime-ubuntu24.04@sha256:2c8193530ecc423e0f123d0c85b68a15d1395adcddabfc943e2523dbfde172e1"

fail() {
    printf 'botified-tts deploy: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 \
        || fail "$1 is required"
}

env_value() {
    local name="$1"
    local count
    count="$(awk -F= -v name="${name}" '$1 == name { count += 1 } END { print count + 0 }' "${ENV_FILE}")"
    [[ "${count}" == "1" ]] \
        || fail "${ENV_FILE} must contain exactly one ${name} entry"
    awk -v name="${name}" '
        index($0, name "=") == 1 {
            print substr($0, length(name) + 2)
        }
    ' "${ENV_FILE}"
}

ensure_env_file() {
    mkdir -p -- "$(dirname -- "${ENV_FILE}")"
    if [[ ! -e "${ENV_FILE}" ]]; then
        : >"${ENV_FILE}"
    fi
    [[ -f "${ENV_FILE}" && ! -L "${ENV_FILE}" ]] \
        || fail "${ENV_FILE} must be a regular file"
    chmod 0600 -- "${ENV_FILE}"

    if ! grep -q '^HOST_GPU=' "${ENV_FILE}"; then
        printf 'HOST_GPU=0\n' >>"${ENV_FILE}"
    fi
    if ! grep -q '^PUBLISHED_PORT=' "${ENV_FILE}"; then
        printf 'PUBLISHED_PORT=8000\n' >>"${ENV_FILE}"
    fi
    if ! grep -q '^BOTIFIED_TTS_API_KEY=' "${ENV_FILE}"; then
        local generated_key
        generated_key="$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')"
        [[ "${generated_key}" =~ ^[0-9a-f]{64}$ ]] \
            || fail "could not generate BOTIFIED_TTS_API_KEY"
        printf 'BOTIFIED_TTS_API_KEY=%s\n' "${generated_key}" >>"${ENV_FILE}"
    fi
    chmod 0600 -- "${ENV_FILE}"
}

compose() {
    HOST_GPU="${HOST_GPU}" \
    PUBLISHED_PORT="${PUBLISHED_PORT}" \
    BOTIFIED_TTS_API_KEY="${BOTIFIED_TTS_API_KEY}" \
    docker compose \
        --file "${COMPOSE_FILE}" \
        "$@"
}

deployment_diagnostics() {
    compose ps >&2 || true
    printf 'View logs with: BOTIFIED_TTS_API_KEY=x docker compose --file %q logs --tail=100 tts\n' \
        "${COMPOSE_FILE}" >&2
}

preflight() {
    local compose_up_help

    [[ "$(uname -s)" == "Linux" ]] \
        || fail "only Linux is supported"
    [[ "$(uname -m)" == "x86_64" ]] \
        || fail "only x86_64 is supported"

    require_command docker
    require_command curl
    require_command nvidia-smi
    require_command timeout

    docker info >/dev/null 2>&1 \
        || fail "Docker daemon is unavailable"
    docker compose version >/dev/null 2>&1 \
        || fail "Docker Compose is unavailable"
    compose_up_help="$(docker compose up --help 2>/dev/null)" \
        || fail "Docker Compose could not report up command capabilities"
    grep -Eq '(^|[[:space:]])--wait([=[:space:]]|$)' <<<"${compose_up_help}" \
        || fail "Docker Compose up must support --wait"
    grep -Eq '(^|[[:space:]])--wait-timeout([=[:space:]]|$)' \
        <<<"${compose_up_help}" \
        || fail "Docker Compose up must support --wait-timeout"

    [[ "${HOST_GPU}" =~ ^[0-9]+$ ]] \
        || fail "HOST_GPU must be a non-negative GPU index"
    [[ "${PUBLISHED_PORT}" =~ ^[0-9]+$ ]] \
        && ((PUBLISHED_PORT >= 1 && PUBLISHED_PORT <= 65535)) \
        || fail "PUBLISHED_PORT must be an integer from 1 to 65535"
    [[ -n "${BOTIFIED_TTS_API_KEY}" ]] \
        && [[ "${BOTIFIED_TTS_API_KEY}" != *[![:ascii:]]* ]] \
        && [[ "${BOTIFIED_TTS_API_KEY}" != [[:space:]]* ]] \
        && [[ "${BOTIFIED_TTS_API_KEY}" != *[[:space:]] ]] \
        || fail "existing BOTIFIED_TTS_API_KEY must be non-empty ASCII without surrounding whitespace"

    local gpu_details
    gpu_details="$(
        timeout 10 nvidia-smi \
            --id="${HOST_GPU}" \
            --query-gpu=index,name,memory.total,compute_cap \
            --format=csv,noheader,nounits 2>/dev/null
    )" || fail "HOST_GPU=${HOST_GPU} does not identify a visible NVIDIA GPU"
    [[ "${gpu_details%%,*}" == "${HOST_GPU}" ]] \
        || fail "HOST_GPU=${HOST_GPU} does not identify an exact physical GPU index"
    printf 'Host GPU: %s\n' "${gpu_details}"

    timeout 120 docker run --rm \
        --gpus "device=${HOST_GPU}" \
        "${CUDA_IMAGE}" \
        nvidia-smi \
            --query-gpu=index,name,memory.total,compute_cap \
            --format=csv,noheader,nounits \
        || fail "fixed CUDA runtime cannot access HOST_GPU=${HOST_GPU}"
}

smoke() {
    local temp_dir
    local wav_file
    local headers_file
    local http_status
    local content_type
    local byte_length
    local riff
    local wave

    temp_dir="$(mktemp -d)"
    wav_file="${temp_dir}/smoke.wav"
    headers_file="${temp_dir}/headers"
    trap 'rm -rf -- "${temp_dir}"' EXIT

    if ! http_status="$(
        printf 'Authorization: Bearer %s\n' "${BOTIFIED_TTS_API_KEY}" \
            | curl \
                --silent \
                --show-error \
                --connect-timeout 5 \
                --max-time 180 \
                --output "${wav_file}" \
                --dump-header "${headers_file}" \
                --write-out '%{http_code}' \
                --header @- \
                --header 'Content-Type: application/json' \
                --data '{"text":"你好，这是 Botified TTS 部署测试。"}' \
                "http://127.0.0.1:${PUBLISHED_PORT}/v1/speech"
    )"; then
        deployment_diagnostics
        fail "speech smoke request failed"
    fi

    [[ "${http_status}" == "200" ]] || {
        deployment_diagnostics
        fail "speech smoke returned HTTP ${http_status}"
    }
    content_type="$(
        awk '
            BEGIN { IGNORECASE = 1 }
            /^content-type:/ {
                sub(/^[^:]*:[[:space:]]*/, "")
                sub(/\r$/, "")
                value = $0
            }
            END { print value }
        ' "${headers_file}"
    )"
    [[ "${content_type}" == "audio/wav" ]] || {
        deployment_diagnostics
        fail "speech smoke returned unexpected Content-Type"
    }

    byte_length="$(wc -c <"${wav_file}")"
    [[ "${byte_length}" -gt 44 ]] || {
        deployment_diagnostics
        fail "speech smoke returned an empty WAV"
    }
    riff="$(dd if="${wav_file}" bs=1 count=4 status=none)"
    wave="$(dd if="${wav_file}" bs=1 skip=8 count=4 status=none)"
    [[ "${riff}" == "RIFF" && "${wave}" == "WAVE" ]] || {
        deployment_diagnostics
        fail "speech smoke returned an invalid RIFF/WAVE file"
    }

    printf 'Botified TTS is ready on port %s (%s-byte WAV smoke passed).\n' \
        "${PUBLISHED_PORT}" "${byte_length}"
    rm -rf -- "${temp_dir}"
    trap - EXIT
}

main() {
    ensure_env_file

    HOST_GPU="$(env_value HOST_GPU)"
    PUBLISHED_PORT="$(env_value PUBLISHED_PORT)"
    BOTIFIED_TTS_API_KEY="$(env_value BOTIFIED_TTS_API_KEY)"

    preflight
    compose config --quiet
    compose build
    if ! compose up -d --wait --wait-timeout 900; then
        deployment_diagnostics
        fail "service did not become healthy"
    fi
    smoke
}

main "$@"
