"""Acquire five CAUCAFall Subject1 non-fall clips with publisher checksums."""
from pathlib import Path
import json,hashlib,httpx
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'video/public_samples/CAUCAFall_negative_5'
CACHE=ROOT/'runtime/fall-corpus-20261101/research'
folders=json.loads((ROOT/'data/dataset_acquisition_20261020/caucafall-folders.json').read_text(encoding='utf-8'))
subject=next(f for f in folders if f['name']=='Subject.1')
selection=['Walk','Sit down','Pick up object','Kneel','Hop']
OUT.mkdir(parents=True,exist_ok=True);manifest={'dataset':'CAUCAFall v5','source':'https://data.mendeley.com/datasets/7w7fccy7ky/5','license':'CC BY 4.0','authors':['Jose Camilo Eraso','Elena Muñoz','Mariela Muñoz','Jesus Pinto'],'scope':'Subject1 activities of daily living, publisher labels; visual verification still required','clips':[]}
with httpx.Client(timeout=90,follow_redirects=True) as client:
 for action in selection:
  folder=next(f for f in folders if f.get('parent_id')==subject['id'] and f['name']==action)
  cache=CACHE/f'negative-S1-{action.replace(" ","-")}-files.json'
  if not cache.exists():
   r=client.get(f'https://data.mendeley.com/public-api/datasets/7w7fccy7ky/files?folder_id={folder["id"]}&version=5&$start=0&$limit=1000');r.raise_for_status();cache.write_bytes(r.content)
  entries=json.loads(cache.read_text(encoding='utf-8'));videos=[e for e in entries if e['filename'].lower().endswith('.avi')];assert len(videos)==1
  e=videos[0];d=e['content_details'];target=OUT/e['filename']
  if not target.exists():
   r=client.get(d['download_url']);r.raise_for_status();assert hashlib.sha256(r.content).hexdigest()==d['sha256_hash'];target.write_bytes(r.content)
  sha=hashlib.sha256(target.read_bytes()).hexdigest();assert sha==d['sha256_hash']
  manifest['clips'].append({'filename':target.name,'action':action,'download_url':d['download_url'],'sha256':sha,'bytes':target.stat().st_size})
  (OUT/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
  print(action,target.name,target.stat().st_size,flush=True)
