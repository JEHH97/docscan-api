"""Run the development ASGI server."""

import uvicorn

uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
