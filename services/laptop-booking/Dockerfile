FROM python:3.11-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
VOLUME /data
ENV DB_PATH=/data/bookings.db
ENV ADMIN_PASSWORD=change-me-in-production
ENV FEISHU_WEBHOOK=
EXPOSE 5000
CMD ["python", "app.py"]
