FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 WORKFLOW_NO_ENV=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY . /app
RUN cp config/workflow_config.example.json config/workflow_config.json && useradd -m workflow && mkdir -p storage && chown -R workflow:workflow /app
USER workflow
CMD ["python", "main.py", "--no-env", "doctor"]
