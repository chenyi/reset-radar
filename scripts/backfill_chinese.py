#!/usr/bin/env python3
"""Manually translate the already-public historical messages using shared budget guards."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import storage
import translations

if not os.environ.get('DATABASE_URL'):
    raise SystemExit('Set the protected production DATABASE_URL first')
db=storage.connect('unused')
try:
    total=len(translations.public_posts(db))
    print(f'Public posts/evidence requiring Chinese reading copies: {total}',flush=True)
    while translations.translate_batch(db):
        pass
    ready=translations.translated(db)
    print(json.dumps({'public_posts':total,'translated':sum(p['post_id'] in ready for p in translations.public_posts(db))}),flush=True)
finally:
    db.close()
