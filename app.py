"""Hyper-V Inventory Web Application (entry point)"""

import os
import sys
from dotenv import load_dotenv

# Add to Python path
basedir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, basedir)

# Load environment variables
load_dotenv()

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        debug=os.getenv("DEBUG", "false").lower() == "true", host="0.0.0.0", port=5000
    )
