# PageMint hosted image (linux/amd64 and linux/arm64).
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# 1 = include Chromium for JavaScript rendering (the "full" setup); 0 = smaller image without it.
ARG WITH_BROWSER=1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PAGEMINT_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[js]" \
 && if [ "$WITH_BROWSER" = "1" ]; then python -m playwright install --with-deps chromium; fi \
 && useradd --create-home --uid 10001 pagemint \
 && mkdir -p /data && chown pagemint /data

USER pagemint
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u,sys; o=u.build_opener(u.ProxyHandler({})); sys.exit(0 if o.open('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["pagemint", "serve", "--http", "--host", "0.0.0.0", "--port", "8000"]
