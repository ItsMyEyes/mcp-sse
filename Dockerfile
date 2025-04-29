# Use an official Python image as the base
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Create logs directory
RUN mkdir -p /app/logs

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV LOG_LEVEL=INFO
ENV LOG_FORMAT=json
ENV LOG_FILE=/app/logs/api.log

# Create a non-root user
RUN adduser --disabled-password --gecos "" app
RUN chown -R app:app /app
USER app

# Expose the port the server will run on
EXPOSE 8000

# Run the command to start the server
CMD ["python", "server.py", "--host=0.0.0.0", "--port=8000"]
