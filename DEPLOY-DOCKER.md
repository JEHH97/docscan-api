# Deploy the public Docker demos

The public demo consists of two single-platform container images:

- `ghcr.io/jehh97/docscan-api:latest` (also published as `vX.Y.Z` and `sha-<short>`). This is built from `Dockerfile.spaces`; it runs the API, RQ worker, and private loopback Redis in one container. It uses temporary `/tmp` storage and demo upload, rate, and retention limits.
- `ghcr.io/jehh97/docscan-mobile-web:latest` (also published as `vX.Y.Z` and `sha-<short>`). This serves the static Expo web export using unprivileged nginx.

Version tags are published for `v*.*.*` Git tags. `latest` is emitted for release tags and manual publication from `main`; the short commit tag is also emitted. Images target `linux/amd64`.

## Pull and run with Compose

Copy the production Compose example and edit the public web/API domains and limits:

```sh
cp deploy/.env.example .env
docker compose --env-file .env -f deploy/docker-compose.prod.yml pull
docker compose --env-file .env -f deploy/docker-compose.prod.yml up -d
```

Both images expose only their service ports to the host: API `7860` and web `8080`. Put a TLS-terminating reverse proxy in front of them, route the public web hostname to `docscan-mobile-web:8080`, and the API hostname to `docscan-api:7860`. Do not expose Redis; it is bound to loopback inside the API container. Configure the web's runtime `API_URL` as the HTTPS API origin, and set API `CORS_ORIGINS` to the exact HTTPS web origin (scheme and hostname, no path or trailing slash). Keep `WEBHOOKS_ENABLED=false` for the public demo.

The API container defaults are `MAX_UPLOAD_BYTES=5242880`, `RATE_LIMIT_PER_MINUTE=10`, `RESULT_TTL_SECONDS=3600`, local temporary storage, and callbacks disabled. `API_KEY` is optional: leave it empty for a browser-accessible demo. When supplied, it protects API requests but cannot be embedded as a public web-client secret. The demo API is publicly usable without `API_KEY`; **do not send sensitive documents to it**. Uploads and scan artifacts are temporary and can disappear on restart.

The web container reads `API_URL` at startup from its environment and writes a public `config.js` before nginx starts. With an empty value, the app defaults to sample mode. Alternatively, build a custom image with `--build-arg EXPO_PUBLIC_API_URL=https://api.example.com`; build-time Expo variables are public and embedded in the web bundle. Runtime `API_URL` is preferred because it lets you change the API endpoint without rebuilding the image. Never put secrets in `API_URL` or any `EXPO_PUBLIC_*` build argument.

## Private package login

Public GHCR packages can be pulled anonymously. If a package is private, authenticate with a GitHub PAT that has `read:packages`:

```sh
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

Do not put the token in `.env`, image build arguments, or a Dockerfile.

## Verify

The API health check calls `/healthz`; the web health check calls `/healthz` on port `8080`.

```sh
curl --fail https://api.example.com/healthz
curl --fail https://docscan.example.com/ -o /dev/null
```

Open the web URL directly and refresh `/history` or `/result/sample-check`; the nginx SPA fallback serves the app shell. The default sample flow works without an API. When the API is configured, confirm browser requests reach the API and that its `CORS_ORIGINS` exactly matches the web origin.

## Image contents and safety

The API image runs as UID 1000 and the web image as UID 101. Neither image contains tests, repository history, environment files, docs, or photo fixtures. Configuration is supplied at runtime; no real credentials are baked into an image. The API is a disposable public demo, not a document-processing service for private information.
