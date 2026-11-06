# Prefect worker image with snapir, Chromium and a virtual display.
# Build from the repository root (see docker-compose.yml):
#   docker compose -f workflows/docker/docker-compose.yml build
FROM prefecthq/prefect:3-python3.11

# xvfb + x11-utils: virtual display for headed scraping (xdpyinfo checks it
# is up); x11vnc: watch or take over that display, e.g. for an
# --interactive scrape that solves a bot check by hand.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git xvfb x11-utils x11vnc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /opt/snapir
COPY . .

# Install snapir with the prefect extra, plus topic-gen (a git dependency,
# needed by the topic-pool flow). If a git dependency is private, pass a GitHub
# token as a BuildKit secret so it never ends up in an image layer:
#   GITHUB_PAT=<token> docker compose -f workflows/docker/docker-compose.yml build
RUN --mount=type=secret,id=github_token,required=false \
    if [ -s /run/secrets/github_token ]; then \
        git config --global url."https://$(cat /run/secrets/github_token)@github.com/".insteadOf "https://github.com/"; \
    fi; \
    uv pip install --system ".[prefect]" \
        "topic-gen @ git+https://github.com/irgroup/topic-gen.git"; \
    status=$?; \
    rm -f /root/.gitconfig; \
    exit $status

RUN playwright install chromium --with-deps

COPY workflows/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV SNAPIR_WORKFLOWS_DIR=/opt/snapir/workflows
WORKDIR /opt/snapir/workflows
ENTRYPOINT ["/entrypoint.sh"]
