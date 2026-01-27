from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.secure_store import load_config, save_config, set_secret, get_secret

KEY = "openai_api_key"
TEST_VALUE = "TEST_OPENAI_123"

cfg = load_config()
old = cfg.get(KEY)

set_secret(cfg, KEY, TEST_VALUE)
save_config(cfg)

cfg2 = load_config()
print("Decrypted:", get_secret(cfg2, KEY))

# Restore l'état précédent
cfg3 = load_config()
if old is None:
    cfg3.pop(KEY, None)
else:
    cfg3[KEY] = old
save_config(cfg3)
