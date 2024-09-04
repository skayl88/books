# Use the official Python image from the Docker Hub
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt requirements.txt

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variables
ENV ANTHROPIC_API_KEY=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

# Copy the rest of the application code into the container
COPY . .

# Expose the port the app runs on
EXPOSE 5000

# Command to run the application
CMD ["python", "app.py"]
