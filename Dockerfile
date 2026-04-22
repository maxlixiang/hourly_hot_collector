FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY hourly_hot_collector.py /app/hourly_hot_collector.py
COPY db.py /app/db.py
COPY config /app/config

CMD ["python", "hourly_hot_collector.py"]
