# Deploy the public API demo to Hugging Face Spaces

For the GHCR Docker images and a paired public web/API deployment, see [DEPLOY-DOCKER.md](DEPLOY-DOCKER.md).

This guide publishes a small, public Docker Space for the existing API. The demo uses local temporary storage, a bundled Redis instance, a separate RQ worker process, and the API process in one container. It does not need a database, persistent storage, or a required API key.

## 1. Create a Space

On Hugging Face, choose **Create new Space** and set:

- **Owner:** your Hugging Face account or organization.
- **Space name:** a name such as `docscan-api-demo`.
- **License:** `MIT`.
- **SDK:** `Docker`.
- **Hardware:** the free CPU option.
- **Visibility:** `Public` for a recruiter-accessible demo.

Do not enable persistent storage; the Space writes only under `/tmp` and its data is intentionally disposable.

## 2. Build the Space repository bundle

From a checkout of this repository, generate a clean build context outside the checkout:

```bash
scripts/make-space-bundle
cd /tmp/opencode/docscan-space
```

The bundle contains the Space `README.md`, a Space-ready `Dockerfile`, the API package, project metadata, and the non-root supervisor configuration. It does not include tests, local environment files, repository history, or sample photos.

Initialize and upload that directory to the Space repository you created:

```bash
git init -b main
git add Dockerfile README.md app deploy pyproject.toml requirements.txt
git commit -m "chore: publish DocScan API Space"
git remote add origin https://huggingface.co/spaces/<account>/<space-name>
git push -u origin main
```

Replace the placeholders with the owner and Space name shown by Hugging Face. These commands apply to your deployment; this repository's changes do not publish or push anything.

## 3. Set Space variables and optional secret

In **Space settings → Variables and secrets**, configure:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `CORS_ORIGINS` | `https://<your-docscan-mobile-project>.vercel.app` |
| Variable | `MAX_UPLOAD_BYTES` | `5242880` |
| Variable | `RATE_LIMIT_PER_MINUTE` | `10` |
| Variable | `RESULT_TTL_SECONDS` | `3600` |
| Variable | `STORAGE_BACKEND` | `local` |
| Variable | `LOCAL_STORAGE_DIR` | `/tmp/docscan-artifacts` |
| Variable | `REDIS_URL` | `redis://127.0.0.1:6379/0` |
| Variable | `WEBHOOKS_ENABLED` | `false` |
| Variable | `PORT` | `7860` |

Use the exact deployed web origin in `CORS_ORIGINS`, without a trailing slash. Add more comma-separated origins only when needed. The container defaults already enforce the upload, rate, and retention limits shown above; listing them here makes the Space configuration explicit.

**Secrets to generate:** none are required for the public demo. Leave `API_KEY` unset so a public browser can use the API. If you deliberately want to test a protected API instead, generate an optional secret with `openssl rand -hex 32`, then add it as a Space secret named `API_KEY`. When `API_KEY` is set, all scan endpoints require the matching `X-API-Key`, so the public demo is locked unless a client supplies it. Never add that secret to the mobile web build: every `EXPO_PUBLIC_*` value is visible to visitors.

Callbacks are disabled in the public Space (`WEBHOOKS_ENABLED=false`) because it does not require an API key. Submitting a `callback_url` returns HTTP 400. Local development and Compose keep callbacks enabled by default; enable them only for a trusted deployment.

Wait for the Space to finish building and show **Running**. Free Spaces may sleep when idle and take time to wake on the next request.

## 4. Verify the deployment

Replace the URL below with the Space URL displayed by Hugging Face:

```bash
export API_ROOT="https://<account>-<space-name>.hf.space"
curl --fail "$API_ROOT/healthz"
curl --fail "$API_ROOT/openapi.json" -o /tmp/opencode/docscan-openapi.json
curl --fail "$API_ROOT/docs" -o /dev/null
```

The health endpoint must return `{"status":"ok"}`. From a source checkout, upload the checked-in synthetic example and poll the returned `status_url` until the state is `done`:

```bash
curl --fail -F 'file=@docs/example-before.png;type=image/png' "$API_ROOT/v1/scans"
curl --fail "$API_ROOT/v1/scans/<scan-id>"
curl --fail "$API_ROOT/v1/scans/<scan-id>/image" -o /tmp/opencode/corrected.png
```

For a protected test Space, add `-H 'X-API-Key: <your-local-secret>'` to each scan request. Scan state and image artifacts expire after one hour, are periodically removed, and are not preserved across Space restarts. A `429` response means the per-client request budget has been exceeded; wait for its one-minute window to pass.

## Local container check

The bundle can also be validated locally without running as root:

```bash
docker build -t docscan-api-space /tmp/opencode/docscan-space
docker run --rm --user 1000:1000 -p 7860:7860 docscan-api-space
```

In another terminal, call `curl --fail http://localhost:7860/healthz` and submit a synthetic page as above.
