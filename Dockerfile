FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install dependencies for Flirc tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    udev \
    libhidapi-hidraw0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Flirc tools
COPY install_flirc_tools.sh .
RUN chmod +x install_flirc_tools.sh && \
    ./install_flirc_tools.sh && \
    rm install_flirc_tools.sh

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

CMD ["python", "run_flirc_mqtt.py"]
