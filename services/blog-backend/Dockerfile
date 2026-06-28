FROM python:3.11-alpine

RUN apk add --no-cache tzdata && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=60 -r requirements.txt

COPY app.py .

RUN adduser -D -s /bin/sh appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

CMD ["python", "app.py"]
