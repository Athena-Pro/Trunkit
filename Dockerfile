FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        primesieve \
        libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Default entrypoint for the nerode CLI; override to 'trunk' for Trunkit
ENTRYPOINT ["nerode"]
CMD ["--help"]
