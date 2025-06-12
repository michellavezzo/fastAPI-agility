
- Reference:
- <https://dorian599.medium.com/fastapi-getting-started-3294efe823a0>
- <https://medium.com/@habbema/construindo-apis-com-fastapi-e-sqlite-99af4cf3b444>

# Create a virtual environment named "fastapi-env"

python -m venv fastapi-env

# Activate the virtual environment

# On Windows

fastapi-env\Scripts\activate

<!-- venv\Scripts\activate -->

# On macOS and Linux

source fastapi-env/bin/activate

<!-- python3 -m venv venv source venv/bin/activate -->

# Install FastAPI and Uvicorn

pip install -r requirements.txt

# Run Your FastAPI Application

uvicorn app.main:app --reload
