FROM --platform=linux/amd64 ubuntu:22.04

# Avoid interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    valgrind \
    gdb \
    gcc \
    make \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install Python dependencies (cacheable layer)
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy Leax source code
COPY srcs/ /app/srcs/

# Copy test files and examples
COPY test_gdb.py /app/
COPY examples/ /app/examples/

# Default command: open bash shell
CMD ["/bin/bash"]