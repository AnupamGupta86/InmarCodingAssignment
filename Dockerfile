# Runtime image only: tests run in CI before this image is built.
FROM python:3.15-slim

# Build version, e.g. 1.0.20261120183012 (see scripts/version.sh).
ARG VERSION=dev

LABEL org.opencontainers.image.title="atm-dispenser" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/AnupamGupta86/InmarCodingAssignment"

ENV ATM_VERSION="${VERSION}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    ATM_HOST=0.0.0.0 \
    ATM_PORT=8080

WORKDIR /app
COPY atm-dispenser/src/ ./

RUN useradd --no-create-home --uid 10001 --shell /usr/sbin/nologin atm
USER 10001

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]

CMD ["python", "-m", "service"]
