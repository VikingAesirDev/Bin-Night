#!/bin/bash

# Install dependencies
pip install -r requirements.txt

# Start Redis (optional, for better caching)
# redis-server &

# Start the Flask application
python app.py
