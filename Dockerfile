# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Make a directory for the app
WORKDIR /app

# Copy requirements first so we can leverage layer caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port FastAPI will run on
EXPOSE 8000

# Use environment variable to control the Uvicorn host/port
ENV HOST=0.0.0.0
ENV PORT=8000

# Start the application with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]