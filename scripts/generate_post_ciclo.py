"""Genera solo el draft thread_post_ciclo."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pipeline._console import setup_utf8
setup_utf8()

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from pipeline.social.copy_generator import generate_post

if __name__ == "__main__":
    draft = generate_post("thread_post_ciclo", force=True)
    print("\n=== DRAFT GENERADO ===")
    print("File:", draft.get("_filePath"))
    content = draft.get("content", {})
    tweets = content.get("tweets", []) or content.get("thread", [])
    for i, t in enumerate(tweets, 1):
        text = t if isinstance(t, str) else t.get("text", "")
        print(f"\n[{i}] {text}")
