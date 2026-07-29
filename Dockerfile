FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PREDICT_API_URL=https://api.predict.fun
ENV PREDICT_WS_URL=wss://ws.predict.fun/ws
ENV LOG_LEVEL=INFO

ENTRYPOINT ["python", "bot.py"]
CMD []
