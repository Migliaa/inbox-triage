# The mock client API (port 8099). Built from the repository root: see compose.yaml.
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.29"

COPY mock_api ./mock_api
COPY fixtures ./fixtures

ENV PYTHONUNBUFFERED=1

EXPOSE 8099
CMD ["uvicorn", "mock_api.server:app", "--host", "0.0.0.0", "--port", "8099"]
