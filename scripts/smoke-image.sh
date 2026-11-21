#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 IMAGE PORT" >&2
  exit 2
fi

image=$1
port=$2
if [[ ! "$port" =~ ^[0-9]+$ ]]; then
  echo "PORT must be an integer between 1 and 65535" >&2
  exit 2
fi
port_number=$((10#$port))
if ((port_number < 1 || port_number > 65535)); then
  echo "PORT must be an integer between 1 and 65535" >&2
  exit 2
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$repo_root/tests/fixtures/synthetic-page.png"
if [[ ! -f "$fixture" ]]; then
  echo "Synthetic scan fixture is missing: $fixture" >&2
  exit 1
fi

container_name="docscan-image-smoke-$$"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/docscan-image-smoke.XXXXXX")"

cleanup() {
  local status=$?
  trap - EXIT
  if ((status != 0)); then
    echo "Container logs from $container_name:" >&2
    docker logs "$container_name" >&2 || true
  fi
  docker stop "$container_name" >/dev/null 2>&1 || true
  docker rm "$container_name" >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
  exit "$status"
}
trap cleanup EXIT

docker run --detach --name "$container_name" --user 1000:1000 \
  --publish "127.0.0.1:$port:7860" "$image" >/dev/null

base_url="http://127.0.0.1:$port"
health_status=starting
for ((elapsed = 0; elapsed < 60; elapsed++)); do
  health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_name" 2>/dev/null || true)"
  if [[ "$health_status" == healthy || "$health_status" == unhealthy ]]; then
    break
  fi
  sleep 1
done
if [[ "$health_status" != healthy ]]; then
  echo "Container did not become healthy within 60 seconds (state: $health_status)" >&2
  exit 1
fi

http_status="$(curl --silent --show-error --output "$tmp_dir/health.json" \
  --write-out '%{http_code}' "$base_url/healthz")"
if [[ "$http_status" != 200 ]]; then
  echo "GET /healthz returned HTTP $http_status, expected 200" >&2
  cat "$tmp_dir/health.json" >&2
  exit 1
fi

curl --silent --show-error --fail \
  --form "file=@$fixture;type=image/png" \
  "$base_url/v1/scans" >"$tmp_dir/submit.json"
scan_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <"$tmp_dir/submit.json")"

scan_status=queued
for ((elapsed = 0; elapsed < 180; elapsed += 2)); do
  curl --silent --show-error --fail \
    "$base_url/v1/scans/$scan_id" >"$tmp_dir/result.json"
  scan_status="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))' \
    <"$tmp_dir/result.json")"
  if [[ "$scan_status" == "done" || "$scan_status" == "failed" ]]; then
    break
  fi
  sleep 2
done

if [[ "$scan_status" != "done" ]]; then
  echo "Scan did not complete successfully within 180 seconds (state: $scan_status)" >&2
  cat "$tmp_dir/result.json" >&2
  exit 1
fi

python3 - "$tmp_dir/result.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as result_file:
    result = json.load(result_file)

text = result.get("result", {}).get("text", "")
expected = ("SAMPLE PAGE", "HELLO WORLD", "OCR DEMO")
missing = [line for line in expected if line not in text]
if missing:
    print(f"OCR output is missing expected text {missing!r}: {text!r}", file=sys.stderr)
    raise SystemExit(1)

print("Container smoke test passed; OCR recognized all synthetic fixture text.")
PY
