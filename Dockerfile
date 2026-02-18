FROM python:3.11-slim

# Install ffmpeg + nodejs
RUN apt-get update && apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    && apt-get clean

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

RUN python -m pip install --upgrade pip

EXPOSE 8080

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080"]
