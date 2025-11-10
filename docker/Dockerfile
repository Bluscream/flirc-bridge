FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    IRTOOLS_PATH=/usr/local/bin/irtools \
    FLIRC_UTIL_PATH=/usr/local/bin/flirc_util

# Install dependencies for Flirc tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    udev \
    libhidapi-hidraw0 \
    libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Flirc tools
COPY install_flirc_tools.sh .
RUN chmod +x install_flirc_tools.sh && \
    ./install_flirc_tools.sh && \
    rm install_flirc_tools.sh

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

CMD ["python", "run_flirc_bridge.py"]
