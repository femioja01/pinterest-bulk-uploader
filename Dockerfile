FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install only Chromium (skip Firefox/WebKit to save space)
RUN playwright install chromium

# Copy application code
COPY src/ src/
COPY login_helper.py .

# Create data directories
RUN mkdir -p /app/data/accounts

EXPOSE 8000

CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
