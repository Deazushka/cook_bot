# Use a stable Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the source code
COPY bot/ ./bot/
COPY Procfile .
COPY runtime.txt .

# Create data directory for SQLite (mapped to a persistent volume on Koyeb)
RUN mkdir -p /app/data

# Environment variable for data directory
ENV DATA_DIR=/app/data

# Command to start the bot
CMD ["python", "bot/main.py"]