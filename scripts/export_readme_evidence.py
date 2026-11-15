"""Publish portable summaries of recorded V1 acceptance fixtures."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
items=[]
for name in ('native','model','nonempty','focus','pause-stop','pause-resume'):
    path=ROOT/'runs'/('external-acceptance-'+name)/'result.json'
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    assert data['success'],name
    fixture=data['fixture']
    items.append({'case':name,'passed':True,'source':fixture['source'],'actual':fixture['text'],
                  'other_field':fixture['other'],'message':data.get('error'),
                  'record_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
for filename,name in (('external-browser-result.json','chrome-single-line'),
                      ('external-browser-multiline-result.json','chrome-multiline-rejected')):
    path=ROOT/'runs'/filename
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    assert data['success'],name
    items.append({'case':name,'passed':True,'source':data['source'],'actual':data['snapshot']['text'],
                  'enter_events':data['snapshot']['enters'],'form_submissions':data['snapshot']['sent'],
                  'selection_utf16':data['snapshot']['selection'],'message':data.get('error'),
                  'record_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
destination=ROOT/'docs'/'evidence'/'v1-acceptance.json'
destination.parent.mkdir(parents=True,exist_ok=True)
destination.write_text(json.dumps({'release':'1.0.0','baseline':'0.4.0','verified_on':'2026-11-10',
    'unit_tests_passed':31,'unit_test_basis':'V1 freeze record in VALIDATION.md',
    'note':'Passed includes expected rejection and interruption cases; it does not always mean source equals actual.',
    'fixtures':items},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
repos=[('browser-use/browser-use','120k'),('OpenHands/OpenHands','93.1k'),
       ('crewAIInc/crewAI','60.7k'),('microsoft/autogen','61.8k'),('huggingface/smolagents','30.3k')]
sources=[]
for name,stars in repos:
    raw=ROOT/'runs'/'readme-research'/(name.replace('/','-')+'.md')
    sources.append({'repository':name,'stars_display':stars,'stars_are_rounded':True,
                    'star_source':'https://github.com/'+name,
                    'readme_source':'https://raw.githubusercontent.com/'+name+'/main/README.md',
                    'readme_sha256':hashlib.sha256(raw.read_bytes()).hexdigest()})
research={'observed_on':'2026-11-10','method':'GitHub repository pages plus official raw README; unauthenticated API was rate limited.',
          'repositories':sources}
(ROOT/'docs'/'evidence'/'readme-sources.json').write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(ROOT/'runs'/'readme-research'/'sources.json').write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Exported %d recorded fixtures and %d README sources' % (len(items),len(sources)))

caret_items=[]
for name in ('append','insert-head','insert-middle','selected'):
    path=ROOT/'runs'/('external-acceptance-1.0.2-'+name)/'result.json'
    if not path.exists():continue
    data=json.loads(path.read_text(encoding='utf-8'))
    assert data['success'],name
    events=[item['data'] for item in data['events'] if item['kind']=='agent']
    actual_inserted=next(item['actual'] for item in events if item['kind']=='complete')
    fixture=data['fixture']
    caret_items.append({'case':name,'passed':True,'initial':fixture['initial'],
        'source':fixture['source'],'full_result':fixture['text'],'task_result':actual_inserted,
        'selection_python':fixture['selection'],'record_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
for browser in ('edge','chrome'):
    path=ROOT/'runs'/('external-browser-1.0.2-'+browser+'-insert-result.json')
    if not path.exists():continue
    data=json.loads(path.read_text(encoding='utf-8'))
    assert data['success'],browser
    events=[item['data'] for item in data['events'] if item['kind']=='agent']
    actual_inserted=next(item['actual'] for item in events if item['kind']=='complete')
    caret_items.append({'case':browser+'-middle','passed':True,'initial':data['initial'],
        'source':data['source'],'full_result':data['snapshot']['text'],'task_result':actual_inserted,
        'selection_utf16':data['snapshot']['selection'],'form_submissions':data['snapshot']['sent'],
        'candidate_rollbacks':sum(item['kind']=='candidate_rollback' for item in events),
        'record_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
if caret_items:
    (ROOT/'docs'/'evidence'/'v1.0.2-caret.json').write_text(json.dumps({
        'release':'1.0.2','verified_on':'2026-11-10','unit_tests_passed':46,'fixtures':caret_items
    },ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Exported %d caret insertion fixtures' % len(caret_items))
