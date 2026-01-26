from pathlib import Path
import sys

# Ajoute ...\MeetingTranslator\src au sys.path pour que "config.*" soit importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.secure_store import load_config, save_config, set_secret, get_secret

cfg = load_config()
set_secret(cfg, "deepl_api_key", "TEST_DEEPL_123")
save_config(cfg)

cfg2 = load_config()
print("Decrypted:", get_secret(cfg2, "deepl_api_key"))
