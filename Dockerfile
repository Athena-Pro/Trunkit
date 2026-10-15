FROM python:3.12-slim AS wheel

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tools ./tools
COPY proofs ./proofs

RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        primesieve \
        libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=wheel /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
 && rm -rf /wheels

# Default entrypoint for the nerode CLI; override to 'trunkit' for Trunkit
ENTRYPOINT ["nerode"]
CMD ["--help"]
