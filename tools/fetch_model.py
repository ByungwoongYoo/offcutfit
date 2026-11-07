"""Fetch the pinned public Apache-2.0 model into an explicitly chosen directory."""
import argparse
from huggingface_hub import snapshot_download

parser=argparse.ArgumentParser()
parser.add_argument("--output",required=True)
args=parser.parse_args()
snapshot_download("sentence-transformers/paraphrase-MiniLM-L3-v2",revision="4ca70771034acceecb2e72475f72050fcdde4ddc",local_dir=args.output,allow_patterns=["config.json","model.safetensors","tokenizer.json","tokenizer_config.json","special_tokens_map.json","vocab.txt"])
