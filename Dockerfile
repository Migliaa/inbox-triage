# The triage service with the decision lab (port 8000).
FROM python:3.12-slim

WORKDIR /app

# torch first and from the CPU-only index: the default Linux wheel bundles the
# CUDA libraries, several GB that a CPU model never uses.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Dependencies before the code, so that editing the code does not reinstall them.
COPY requirements-base.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY web ./web
COPY evals ./evals
COPY fixtures ./fixtures

# The decision model is downloaded here on first start; compose.yaml keeps it in a volume.
ENV HF_HOME=/models \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "src.service:app", "--host", "0.0.0.0", "--port", "8000"]
