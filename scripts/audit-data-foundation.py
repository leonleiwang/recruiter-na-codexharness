#!/usr/bin/env python3
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def sha(p):
 h=hashlib.sha256();
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()

def fail(msg):
 print(f"DATA_FOUNDATION_FAIL: {msg}", file=sys.stderr); raise SystemExit(2)

profiles=json.loads((ROOT/'data/generated/profiles/profile_library.json').read_text(encoding='utf-8'))
pm=json.loads((ROOT/'data/manifests/profile_sources.json').read_text(encoding='utf-8'))
pdfs=sorted((ROOT/'data/source/profiles/originals').glob('*.pdf'))
if profiles.get('meta',{}).get('total') != len(pdfs) or len(profiles.get('profiles',[])) != len(pdfs): fail('profile count mismatch')
by={x['file_name']:x for x in pm['sources']}
for p in pdfs:
 if by.get(p.name,{}).get('sha256') != sha(p): fail(f'profile hash mismatch: {p.name}')

jd=json.loads((ROOT/'data/generated/jd/jd_library.json').read_text(encoding='utf-8'))
jm=json.loads((ROOT/'data/manifests/jd_source.json').read_text(encoding='utf-8'))
xlsx=ROOT/'data/source/jd/岗位清单及JD库.xlsx'
if jm.get('sha256') != sha(xlsx): fail('JD source hash mismatch')
if jd.get('total') != len(jd.get('entries',[])): fail('JD count mismatch')
if jd.get('source_sha256') != sha(xlsx): fail('JD generated source hash mismatch')

rm=json.loads((ROOT/'data/manifests/resume_sources.json').read_text(encoding='utf-8'))
if rm.get('production_authority') != 'MYSQL': fail('resume authority is not MYSQL')
if rm.get('complete_backup_of_production_mysql') is not False: fail('resume source pack incorrectly marked complete')
if (ROOT/'workspace/resume_db.json').exists(): fail('legacy workspace resume DB exists')
print(json.dumps({
 'profile': {'status':'ok','count':len(pdfs),'authority':'VERIFIED_SOURCE_PDF'},
 'jd': {'status':'ok','count':jd['total'],'authority':'VERIFIED_SOURCE_XLSX'},
 'resume': {'status':'manifest_ok','authority':'MYSQL','private_source_files':rm['candidate_source_files'],'complete_backup':False},
}, ensure_ascii=False, indent=2))
print('DATA_FOUNDATION_PASS')
