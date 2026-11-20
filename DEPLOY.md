# Deployment notes

## Docker Compose

The default local stack runs an API process, a separate RQ worker, Redis, and MinIO. Compose selects the S3-compatible backend and creates the artifact bucket automatically on first storage access. Override demo credentials and application settings through an untracked `.env` file for a non-local environment.

## Hugging Face Spaces

`Dockerfile.spaces` is a single-container demo image that starts Redis, the RQ worker, and the API under supervisord. It listens on port `7860`, uses local artifact storage under `/data/artifacts`, and is intended for a small demo rather than a horizontally scaled deployment. Configure `API_KEY` and any deployment-specific limits through the Space's secret/environment settings. Keep the platform's persistent storage option enabled if submitted artifacts must outlive a restart.

Build locally with:

```bash
docker build -f Dockerfile.spaces -t docscan-spaces .
docker run --rm -p 7860:7860 docscan-spaces
```

A production deployment should use private managed Redis and durable S3-compatible storage, restrict CORS to the client origins, and set a non-empty API key.
