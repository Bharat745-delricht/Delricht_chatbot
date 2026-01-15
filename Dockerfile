FROM python:3.10-slim

# Force rebuild by changing this timestamp: 2025-12-18T20:30:00Z - Fixed CRIO availability with multi-day query
ARG BUILD_DATE=unknown
ARG BUILD_VERSION=unknown

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .
COPY config.py .
COPY api/ api/
COPY core/ core/
COPY models/ models/
COPY static/ static/

# Clear any Python cache files to ensure fresh imports
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
RUN find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Set environment variables
ENV PYTHONPATH=/app
ENV PORT=8080

# Expose port
EXPOSE 8080

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]