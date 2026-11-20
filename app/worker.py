"""Dedicated RQ worker process."""

from redis import Redis
from rq import Queue, Worker

from app.config import get_settings


def main() -> None:
    """Run the document queue worker until interrupted."""
    connection = Redis.from_url(get_settings().redis_url)
    worker = Worker([Queue("scans", connection=connection)], connection=connection)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
