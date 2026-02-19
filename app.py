"""Hyper-V Inventory Web Application (entry point)"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from hyperv_inventory.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        debug=os.getenv("DEBUG", "false").lower() == "true", host="0.0.0.0", port=5000
    )
