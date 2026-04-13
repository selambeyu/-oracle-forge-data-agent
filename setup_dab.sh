#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLBOX_BIN="${ROOT_DIR}/bin/toolbox"
TOOLS_CONFIG="${ROOT_DIR}/mcp/tools.yaml"
LOG_FILE="${ROOT_DIR}/toolbox.log"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
fi

TOOLBOX_URL="${TOOLBOX_URL:-http://127.0.0.1:5000}"

stripped_url="${TOOLBOX_URL#http://}"
stripped_url="${stripped_url#https://}"
host_port="${stripped_url%%/*}"
address="${host_port%%:*}"
port="${host_port##*:}"

if [[ "${host_port}" == "${address}" ]]; then
  if [[ "${TOOLBOX_URL}" == https://* ]]; then
    port="443"
  else
    port="80"
  fi
fi

if [[ ! -x "${TOOLBOX_BIN}" ]]; then
  echo "Toolbox binary not found or not executable: ${TOOLBOX_BIN}"
  exit 1
fi

if [[ ! -f "${TOOLS_CONFIG}" ]]; then
  echo "Toolbox config not found: ${TOOLS_CONFIG}"
  exit 1
fi

pkill -f "toolbox.*mcp/tools.yaml" >/dev/null 2>&1 || true

nohup "${TOOLBOX_BIN}" \
  --config "${TOOLS_CONFIG}" \
  --address "${address}" \
  --port "${port}" \
  serve \
  --enable-api \
  --toolbox-url "${TOOLBOX_URL}" \
  >"${LOG_FILE}" 2>&1 &

toolbox_pid=$!
health_url="${TOOLBOX_URL%/}/v1/tools"
startup_ok=false

for _ in {1..10}; do
  if ! kill -0 "${toolbox_pid}" >/dev/null 2>&1; then
    break
  fi

  if curl --silent --show-error --fail --max-time 2 "${health_url}" >/dev/null 2>&1; then
    startup_ok=true
    break
  fi

  sleep 1
done

if [[ "${startup_ok}" != "true" ]]; then
  echo "Toolbox failed to become healthy at ${health_url}"
  echo "Config: ${TOOLS_CONFIG}"
  echo "Log: ${LOG_FILE}"
  if [[ -s "${LOG_FILE}" ]]; then
    echo
    echo "Recent toolbox log output:"
    tail -n 40 "${LOG_FILE}"
  fi
  exit 1
fi

echo "Toolbox started at ${TOOLBOX_URL}"
echo "Config: ${TOOLS_CONFIG}"
echo "Log: ${LOG_FILE}"
echo "PID: ${toolbox_pid}"
