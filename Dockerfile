FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if required by your packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    software-properties-common \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy over requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose the port Streamlit will run on (Cloud Run defaults to 8080)
EXPOSE 8080

# Configure Streamlit to run on port 8080 and bind to all interfaces
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
