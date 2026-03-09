FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/data/sqlite.db
ENV JWT_SECRET=change-me
ENV JWT_ALGORITHM=HS256
ENV JWT_EXPIRE_MINUTES=60

EXPOSE 5000

CMD ["python", "app/app.py"]
