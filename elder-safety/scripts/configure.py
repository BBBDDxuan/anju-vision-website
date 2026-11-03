"""Generate local secrets without printing them or overwriting an existing .env."""
from pathlib import Path
import secrets
ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / ".env.example").read_text(encoding="utf-8")
text = text.replace("REPLACE_WITH_RANDOM_USER_TOKEN_AT_LEAST_24_CHARS", secrets.token_urlsafe(32))
text = text.replace("REPLACE_WITH_RANDOM_DEVICE_TOKEN_AT_LEAST_24_CHARS", secrets.token_urlsafe(32))
with (ROOT / ".env").open("x", encoding="utf-8") as handle:
    handle.write(text)
print("Created local .env; secrets not printed. Default providers are mock.")
