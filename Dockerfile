# One image, four services: pick one with the command, e.g. `docker run IMAGE mcp`.
# AgentCore Runtime requires linux/arm64 images.
FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd -m app && mkdir -p /data && chown app /data
USER app
ENV SQLITE_PATH=/data/appointments.db

# 8000 MCP (/mcp) | 9000 A2A | 8080 AgentCore HTTP runtime (/invocations, /ping) | 8081 REST API
EXPOSE 8000 9000 8080 8081
ENTRYPOINT ["appointment-app"]
CMD ["orchestrator"]
