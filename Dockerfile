# Runtime image only: tests run in CI before this image is built.
FROM python:3.14.8-slim

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

# Patch the OS packages, and remove pip and setuptools: the app uses only the standard library,
# so the image needs no packaging tools (and they bring their own vulnerabilities).
# hadolint ignore=DL3005
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && site_packages="$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib"))')" \
    && rm -rf "${site_packages:?}"/* /usr/local/lib/python3*/ensurepip /usr/local/bin/pip* \
    && useradd --no-create-home --uid 10001 --shell /usr/sbin/nologin atm
USER 10001

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]

CMD ["python3", "service.py"]
