# The image `sandbox_run_python` executes code in. Built once:  make sandbox-image
# Deliberately minimal: Python + a few analysis libraries, no shell tools an agent could abuse.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir "pandas>=2.2,<3" "python-dateutil>=2.9" \
    && rm -rf /root/.cache

# The server runs containers as 65534:65534 with a tmpfs /work; nothing else is writable.
WORKDIR /work
CMD ["python", "-c", "print('foreman sandbox')"]
