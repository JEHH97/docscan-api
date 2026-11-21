---
title: DocScan API Demo
emoji: 📄
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# DocScan API demo

This public Space is a small demonstration of asynchronous document cleanup and OCR. It accepts synthetic or non-sensitive JPEG, PNG, and WebP images up to 5 MiB. Requests are limited, and scan state and generated image artifacts expire after one hour. Space storage is temporary and is not retained across restarts.

The API is public by default for the demo. Do not upload private documents. The optional `API_KEY` secret is intentionally unset in the public configuration; defining it requires every scan request to provide `X-API-Key`. Webhook callbacks are disabled (`WEBHOOKS_ENABLED=false`) because this public demo has no API key; callback submissions receive HTTP 400.

Open `/docs` for the API contract or `/healthz` to check availability. A free Space may sleep while idle and take time to wake on its next request.
