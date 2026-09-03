# Use a lightweight Python base image
FROM python:3.11-slim

# Force Python to print logs directly to the console
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# Copy your requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your project files (manager.py, scrapers, etc.)
COPY . .

# Tell the container to run your orchestrator script when it starts
CMD ["python", "manager.py"]