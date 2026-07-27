from dotenv import load_dotenv

# Load .env before any module reads os.getenv at import time (e.g. app.db).
load_dotenv()
