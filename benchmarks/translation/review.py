"""Create anonymous review packets and export allowlisted benchmark data."""
from __future__ import annotations
import argparse
import csv
import hashlib
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import shutil
from bench import DEFAULT_WORK, HERE, RUBRIC, read, write, now, digest, setup_cost, cost, request_rates


def recorded_cost(work, task_id, model):
    """Expose the priced subset without presenting missing requests as free."""
    path = work/'usage'/f'{task_id}.jsonl'
    if not path.exists():
        return {'recordedCostUsd': None, 'usageKnownRequests': None, 'usageMissingRequests': None}
    total = 0
    known = missing = 0
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event.get('input') is None or event.get('output') is None:
            missing += 1
            continue
        hit = event.get('hit')
        if hit is None and event.get('miss') is not None:
            hit = max(0, event['input'] - event['miss'])
        amount = cost({'tokens': {'input': event['input'], 'output': event['output'], 'availability': 'complete'},
                       'providerCache': {'hitTokens': hit, 'availability': 'complete' if hit is not None else 'unavailable'}},
                      request_rates(model, event['input'], event['timestamp']))
        if amount is None:
            missing += 1
            continue
        known += 1
        total += amount
    return {'recordedCostUsd': total if known else None, 'usageKnownRequests': known, 'usageMissingRequests': missing}


def normalize(text):
    return re.sub(r'\W+', '', text.lower())


def paragraphs(work, task_id):
    files = list((work/'tasks'/task_id).rglob('paragraph-recovery.sqlite3'))
    if len(files) != 1:
        return []
    with sqlite3.connect(f'{files[0].as_uri()}?mode=ro', uri=True) as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT payload FROM paragraphs')]


def packets(work, only=None):
    import fitz
    snapshot = read(work/'snapshot.json')
    runs = read(work/'runs.json')
    map_path = work/'private-mapping.json'
    mappings = read(map_path) if map_path.exists() else {}
    for paper in snapshot['papers']:
        if only and only != paper['id']:
            continue
        rows = [r for r in runs if r['paper']==paper['id'] and not r.get('pilot')]
        if len(rows) != len(snapshot['models']) or any(r['status'] not in {'completed','incomplete','failed','cancelled','unavailable'} for r in rows):
            print('Not ready:',paper['id'])
            continue
        folder=work/'review'/paper['id']
        if (folder/'packet.json').exists():
            print('Frozen review packet exists:',paper['id'])
            continue
        ids=[m['id'] for m in snapshot['models']]
        random.SystemRandom().shuffle(ids)
        mappings[paper['id']]={chr(65+i): model for i,model in enumerate(ids)}
        write(map_path,mappings)
        selection=read(work/'selection'/(paper['id']+'.json'))
        packet={'paper':paper['id'],'title':paper['title'],'judge':'gpt-6-sol','rubric':RUBRIC,
                'selectionSha256':paper['selectionSha256'],'samples':selection,'candidates':[]}
        source=fitz.open(work/'papers'/(paper['id']+'.pdf'))
        folder.mkdir(parents=True,exist_ok=True)
        for number in paper['visualPages']:
            source[number-1].get_pixmap(matrix=fitz.Matrix(1.3,1.3)).save(folder/f'source-page-{number}.png')
        source.close()
        for alias, model in mappings[paper['id']].items():
            row=next(r for r in rows if r['model']==model)
            entries=paragraphs(work,row['taskId']) if row.get('taskId') else []
            samples=[]
            for anchor in selection:
                same_page=[e for e in entries if e.get('page')==anchor['page']]
                # Supply every paragraph on the selected page as alignment evidence;
                # automatic nearest match is only a suggestion, never treated as truth.
                scored=[(SequenceMatcher(None,normalize(anchor['source']),normalize(e.get('source',''))).ratio(),e) for e in same_page]
                score, match=max(scored,key=lambda x:x[0],default=(0,{}))
                samples.append({'id':anchor['id'],'page':anchor['page'],'matchConfidence':round(score,3),
                                'suggestedSource':match.get('source'),'suggestedTranslation':match.get('translation'),
                                'pageParagraphs':[{'source':e.get('source'),'translation':e.get('translation'),
                                                   'protectedInput':e.get('input'),'status':e.get('status')} for e in same_page]})
            candidate={'alias':alias,'samples':samples,'pdfAvailable':False,'visuals':[]}
            pdf=work/'outputs'/(row.get('taskId','missing')+'.pdf')
            if pdf.exists():
                doc=fitz.open(pdf)
                candidate['pdfAvailable']=True
                candidate['outputPages']=len(doc)
                # The plugin normally uses side-by-side pages. Also support
                # alternating dual output without guessing other page mappings.
                for number in paper['visualPages']:
                    if len(doc)==paper['pages']:
                        indices=[number-1]
                    elif len(doc)==paper['pages']*2:
                        indices=[2*(number-1),2*(number-1)+1]
                    else:
                        indices=list(range(len(doc)))
                    for index in indices:
                        name=f'{alias}-output-page-{index+1}.png'
                        if not (folder/name).exists():
                            doc[index].get_pixmap(matrix=fitz.Matrix(1.3,1.3)).save(folder/name)
                        candidate['visuals'].append(name)
                doc.close()
            packet['candidates'].append(candidate)
        write(folder/'packet.json',packet)
        instruction=(HERE/'review-instructions.md').read_text(encoding='utf-8')
        (folder/'INSTRUCTIONS.md').write_text(instruction,encoding='utf-8')
        print('Review packet frozen:',paper['id'],flush=True)


def validate_review(packet, review):
    expected={c['alias'] for c in packet['candidates']}
    if review.get('judge')!='gpt-6-sol' or review.get('paper')!=packet['paper']:
        raise ValueError('Review identity mismatch')
    candidates=review.get('candidates',[])
    if len(candidates)!=len(expected) or {c['alias'] for c in candidates}!=expected:
        raise ValueError('Missing or duplicate candidates')
    samples={s['id'] for s in packet['samples']}
    for candidate in candidates:
        ratings=candidate.get('samples',[])
        if len(ratings)!=len(samples) or {s['id'] for s in ratings}!=samples:
            raise ValueError('All frozen samples must be reviewed')
        for rating in ratings:
            if rating.get('unassessable'):
                if not rating.get('reason'):
                    raise ValueError('Unassessable samples need evidence')
                continue
            scores=rating['scores']
            if set(scores)!=set(RUBRIC):
                raise ValueError('Rubric mismatch')
            for key,maximum in RUBRIC.items():
                v=scores[key]
                if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=maximum:
                    raise ValueError('Invalid score')
            if sum(scores.values())<100 and not rating.get('issues'):
                raise ValueError('Deductions require evidence')
            for issue in rating.get('issues',[]):
                if not all(issue.get(k) for k in ('severity','category','sourceEvidence','translationEvidence','explanation')):
                    raise ValueError('Incomplete issue evidence')
                if issue['severity'] not in {'minor','major','critical'} or issue['category'] not in {'translation','parsing','layout'}:
                    raise ValueError('Unknown severity or issue category')


def audit_selection(packet, review, seed=20260929):
    all_items=[(c['alias'],s['id']) for c in review['candidates'] for s in c['samples']]
    selected=set()
    for c in review['candidates']:
        for s in c['samples']:
            if any(i.get('severity') in ('major','critical') for i in s.get('issues',[])):
                selected.add((c['alias'],s['id']))
        for layout in c.get('layout',[]):
            if layout.get('status')=='issue':
                selected.add((c['alias'],'layout-'+str(layout['page'])))
    remaining=[item for item in all_items if item not in selected]
    selected.update(random.Random(str(seed)+packet['paper']).sample(remaining,math.ceil(len(remaining)*.2)))
    return [{'alias':a,'sample':s,'status':'pending'} for a,s in sorted(selected)]


def revision_summary(value):
    """Publish concise changes without embedding full raw evidence objects."""
    if value is None:
        return '未记录此项。'
    if isinstance(value,str):
        return value
    if 'scores' in value:
        return f"总分 {sum(value['scores'].values())}/100。" + ' '.join(i['explanation'] for i in value.get('issues',[]))
    return str(value.get('status',''))+'：'+str(value.get('evidence',''))


def export(work, destination, private=False):
    if private and not destination.resolve().is_relative_to(DEFAULT_WORK.parent.resolve()):
        raise ValueError('Private exports must stay inside the ignored .local-dev directory')
    snapshot=read(work/'snapshot.json')
    runs=read(work/'runs.json') if (work/'runs.json').exists() else []
    mappings=read(work/'private-mapping.json') if (work/'private-mapping.json').exists() else {}
    reviews={}
    for paper in snapshot['papers']:
        folder=work/'review'/paper['id']
        if (folder/'review.json').exists():
            packet,review=read(folder/'packet.json'),read(folder/'review.json')
            validate_review(packet,review)
            audit=read(folder/'audit.json') if (folder/'audit.json').exists() else []
            required={(r['alias'],r['sample']) for r in audit_selection(packet,review)}
            done={(r['alias'],r['sample']) for r in audit if r.get('status')=='verified' and r.get('note')}
            reviews[paper['id']]={'review':review,'verified':required.issubset(done),'audit':audit}
    results=[]
    for r in runs:
        paper=next(p for p in snapshot['papers'] if p['id']==r['paper'])
        alias=next((a for a,m in mappings.get(r['paper'],{}).items() if m==r['model']),None)
        bundle=reviews.get(r['paper']) if not r.get('pilot') else None
        candidate=next((c for c in bundle['review']['candidates'] if c['alias']==alias),None) if bundle else None
        metrics=r.get('metrics') or {}
        failures=[]
        if r.get('taskId'):
            for entry in paragraphs(work,r['taskId']):
                if entry.get('status')!='failed':continue
                source=entry.get('source','')
                failures.append({'page':entry.get('page'),'paragraphId':entry.get('paragraphId'),
                    'errorType':entry.get('errorType'),'statusCode':entry.get('statusCode'),
                    'sourceSha256':hashlib.sha256(source.encode()).hexdigest(),
                    'sourceKind':'url-footnote' if re.match(r'^\d*https?://',source) else 'text',
                    'unchangedTranslation':str(entry.get('reason','')).startswith('unchanged_translation')})
        public={'paper':r['paper'],'model':r['model'],'alias':alias,'phase':'pilot' if r.get('pilot') else 'full','taskId':r.get('taskId'),
                'status':r['status'],'seconds':r.get('elapsedSeconds'),'costUsd':r.get('costUsd'),
                'stopReason':r.get('stopReason'),
                'costBasis':r.get('costBasis'),'rejectedAuthRequests':r.get('rejectedAuthRequests',0),
                'tokens':metrics.get('tokens'),'requests':metrics.get('requests'),
                'stageDurations':metrics.get('stageDurations'), 'configSha256':r.get('configSha256'),
                'summary':r.get('translationSummary'),'outputSha256':r.get('outputSha256'),
                'quality':None,'reviewStatus':'pending','samples':[],'layout':[]}
        public['failedParagraphs']=failures
        model=next(m for m in snapshot['models'] if m['id']==r['model'])
        public.update(recorded_cost(work,r.get('taskId'),model))
        if candidate:
            scores=[sum(s['scores'].values()) for s in candidate['samples'] if not s.get('unassessable')]
            public['quality']=round(sum(scores)/len(scores),2) if len(scores)==12 else None
            public['reviewStatus']='verified' if bundle['verified'] else 'unverified'
            public['assessedSamples']=len(scores)
            # Full copyrighted excerpts stay local unless redistribution was verified.
            for s in candidate['samples']:
                anchor=next(a for a in read(folder_for(work,r['paper'])/'packet.json')['samples'] if a['id']==s['id'])
                public_sample={'id':s['id'],'page':anchor['page'],'scores':s.get('scores'),
                    'unassessable':s.get('unassessable',False),'reason':s.get('reason'),
                    'issues':[{k:i.get(k) for k in ('severity','category','explanation')} for i in s.get('issues',[])]}
                if private:
                    packet=read(folder_for(work,r['paper'])/'packet.json')
                    sample=next(x for c in packet['candidates'] if c['alias']==alias for x in c['samples'] if x['id']==s['id'])
                    public_sample.update(source=anchor['source'],pageParagraphs=sample['pageParagraphs'],issues=s.get('issues',[]))
                public['samples'].append(public_sample)
            public['layout']=candidate.get('layout',[])
            public['audit']=[a for a in bundle['audit'] if a['alias']==alias]
            public['reviewSummary']=candidate.get('summary')
            public['reviewSha256']=digest(folder_for(work,r['paper'])/'review.json')
            initial_path=folder_for(work,r['paper'])/'review.initial.json'
            if initial_path.exists():
                initial=read(initial_path)
                initial_candidate=next(c for c in initial['candidates'] if c['alias']==alias)
                public['initialReviewSha256']=digest(initial_path)
                public['initialScores']=[{'id':s['id'],'scores':s.get('scores'),'unassessable':s.get('unassessable',False)} for s in initial_candidate['samples']]
            revisions_path=folder_for(work,r['paper'])/'revisions.json'
            if revisions_path.exists():
                revisions=read(revisions_path)
                public['reviewRevisions']=[{'target':c['target'],'initial':revision_summary(c.get('initial')),
                    'revised':revision_summary(c.get('revised'))} for c in revisions.get('changes',[]) if alias in c.get('target','').split('.')[0].split('/')]
            if private:
                public['visuals']=[]
                folder=folder_for(work,r['paper'])
                assets=destination/'assets'/r['paper'];assets.mkdir(parents=True,exist_ok=True)
                for png in list(folder.glob('source-page-*.png'))+list(folder.glob(alias+'-output-page-*.png')):
                    shutil.copy2(png,assets/png.name)
                    public['visuals'].append('assets/'+r['paper']+'/'+png.name)
        results.append(public)
    bound=read(work/'billing-bound.json') if (work/'billing-bound.json').exists() else None
    data={'schemaVersion':1,'visibility':'private' if private else 'public','generatedAt':now(),'commit':snapshot['commit'],
          'benchmarkCodeCommit':snapshot.get('benchmarkCodeCommit'),
          'runtime':snapshot.get('runtime'),'python':snapshot.get('python'),'platform':snapshot.get('platform'),
          'preparedAt':snapshot['preparedAt'],'config':snapshot['config'],'rubric':RUBRIC,
          'preparationSeconds':snapshot.get('preparationSeconds'),
          'budgetResetAt':snapshot.get('budgetResetAt'),'sourceFingerprints':snapshot.get('sourceFingerprints'),
          'selectionAmendment':snapshot.get('selectionAmendment'),
          'judge':'gpt-6-sol','reviewMethod':'GPT-6 Sol 子代理评审，主代理复核',
          'findings':snapshot.get('findings',[]),
          'zoteroValidation':snapshot.get('zoteroValidation','pending'),
          'pricingSource':snapshot['pricingSource'],'pricingCheckedAt':snapshot.get('pricingCheckedAt'),
          'models':[{k:m[k] for k in ('id','name','input','output','cacheRead','priceDetails','accessStatus','accessCheckedAt') if k in m} for m in snapshot['models']],
          'papers':[{k:p.get(k) for k in ('id','title','arxiv','sha256','pages','selectionSha256','redistributionApproved','sourceLicense','downloadSeconds')} for p in snapshot['papers']],
          'publishFullPdfs':snapshot.get('publishFullPdfs',False),
          'budgetUsd':snapshot['budgetUsd'],'measuredSpendUsd':setup_cost(work,snapshot)+sum(r.get('costUsd') or 0 for r in runs),
          'budgetAccountDeltaUsd':bound['deltaUsd'] if bound else None,'budgetObservedAt':bound['observedAt'] if bound else None,
          'unknownCostTasks':sum(bool(r.get('taskId')) and r.get('costUsd') is None for r in runs),
          'results':results,'limitations':['经典机器学习论文；首轮每项一次，缺失PDF的三项各补跑一次，不代表所有学科或稳定速度。',
             '经典论文可能是模型熟悉的内容；12 个片段的细小分差不应视为稳定优势。',
             'GPT-6 Sol 与 GPT-6 Luna 同系列，可能存在评审偏差。',
             '费用按请求 token、时间和上下文分档估算；未知缓存按未命中保守估算，不是供应商账单。',
             *(['本轮资源准备总耗时未单独计时，记为未知；表中耗时从任务提交开始计算。'] if snapshot.get('preparationSeconds') is None else []),
             *(['完整 PDF 按用户明确要求公开，用于本次翻译测评；原论文作者和来源见测试文件，译文不代表作者认可。'] if snapshot.get('publishFullPdfs') else ['尚未确认完整论文译文的再分发许可，原文与完整 PDF 保存在本地。'])]}
    integrate_supplements(work,destination,data,private)
    write(destination/'results.json',data)
    export_pdf_manifest(work,destination,snapshot,runs,private)
    destination.mkdir(parents=True,exist_ok=True)
    fields=['phase','paper','model','status','quality','reviewStatus','seconds','costUsd','recordedCostUsd','usageKnownRequests','usageMissingRequests','taskId','outputSha256']
    with (destination/'results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(data['results'])
    print('Exported',len(results),'real task results')


def integrate_supplements(work, destination, data, private=False):
    """Select audited supplementary PDFs, preserving first runs and total effort."""
    index_path=work/'supplement-review-index.json'
    if not index_path.exists():
        return
    data['originalResults']=list(data['results'])
    data['supplementalResults']=[]
    for item in read(index_path):
        job=Path(item['path']).resolve()
        if not job.is_relative_to((work/'supplement-reviews').resolve()):
            raise ValueError('Supplement review must remain in the local review workspace')
        temporary=job/('export-private' if private else 'export-public')
        export(job,temporary,private)
        candidate=read(temporary/'results.json')['results'][0]
        if candidate['taskId']!=item['taskId']:
            raise ValueError('Supplement task provenance mismatch')
        if candidate['reviewStatus']!='verified':
            raise ValueError('Supplement must be audited before publication')
        original=next(r for r in data['originalResults'] if r['paper']==candidate['paper'] and r['model']==candidate['model'])
        source=next(r for r in read(work/'supplemental-runs.json') if r['taskId']==item['taskId'])
        if source['supplementalTo']!=original['taskId'] or source['outputSha256']!=candidate['outputSha256']:
            raise ValueError('Supplement output provenance mismatch')
        if private:
            for visual in candidate.get('visuals',[]):
                target=destination/'supplements'/str(item['job'])/visual
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(temporary/visual,target)
            candidate['visuals']=['supplements/'+str(item['job'])+'/'+v for v in candidate.get('visuals',[])]
        data['supplementalResults'].append(candidate)
        attempts=[original,candidate]
        effective={**candidate,'alias':original['alias'],'reviewAlias':candidate['alias'],
                   'selectionBasis':'reviewed_supplement','attempts':attempts,'attemptCount':2}
        for field in ('costUsd','seconds','usageKnownRequests','usageMissingRequests'):
            effective[field]=sum(r[field] for r in attempts) if all(r.get(field) is not None for r in attempts) else None
        known=[r['recordedCostUsd'] for r in attempts if r.get('recordedCostUsd') is not None]
        effective['recordedCostUsd']=sum(known) if known else None
        effective['costBasis']='All first-run and supplementary attempts; unknown usage remains unknown.'
        data['results']=[effective if r is original else r for r in data['results']]
    all_attempts=data['originalResults']+data['supplementalResults']
    data['measuredSpendUsd']=setup_cost(work,read(work/'snapshot.json'))+sum(r.get('costUsd') or 0 for r in all_attempts)
    data['unknownCostTasks']=sum(r.get('costUsd') is None for r in all_attempts)
    data['resultSelection']='Audited supplements replace missing PDFs; costs and time include every attempt; first-run records retained.'
    data['limitations'].append('三项未生成 PDF 的任务补跑一次；对应质量和完成状态采用已复核补跑，费用与耗时累计首轮及补跑。不同模型尝试次数不同。')


def export_pdf_manifest(work, destination, snapshot, runs, private=False):
    """Publish exact task artifacts only within the selected disclosure boundary."""
    if private and not destination.resolve().is_relative_to(DEFAULT_WORK.parent.resolve()):
        raise ValueError('Private PDFs must stay inside the ignored .local-dev directory')
    documents=[]
    supplement_path=work/'supplemental-runs.json'
    supplements=read(supplement_path) if supplement_path.exists() else []
    for run in runs:
        if run.get('pilot') or not run.get('taskId'):
            continue
        original=run
        supplement=next((r for r in supplements if r.get('supplementalTo')==run['taskId']),None)
        if supplement and supplement.get('outputSha256'):
            run=supplement
        task=run['taskId']
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',task):
            raise ValueError('Invalid PDF task identifier')
        paper=next(p for p in snapshot['papers'] if p['id']==run['paper'])
        source=work/'outputs'/f'{task}.pdf'
        item={'paper':run['paper'],'model':run['model'],'taskId':task,
              'sha256':run.get('outputSha256'),'pages':None,'bytes':None,'url':None,
              'availability':'not_generated','status':run['status'],
              'supplemental':run is not original}
        if supplement:
            item['supplementaryRun']={k:supplement[k] for k in
                ('taskId','supplementalTo','status','reviewStatus','reviewSha256','elapsedSeconds','costUsd','outputSha256','stopReason')
                if k in supplement}
        if item['sha256']:
            if not source.exists():
                item['availability']='missing'
            else:
                if digest(source)!=item['sha256']:
                    raise ValueError(f'PDF checksum changed for task {task}')
                item.update(pages=paper['pages'],bytes=source.stat().st_size,availability='local_only')
                if private or paper.get('redistributionApproved') is True or snapshot.get('publishFullPdfs') is True:
                    relative=f'pdfs/{task}.pdf'
                    (destination/'pdfs').mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source,destination/relative)
                    item.update(url=relative,availability='ready')
        documents.append(item)
    bound=read(work/'billing-bound.json') if (work/'billing-bound.json').exists() else None
    write(destination/'pdfs.json',{'schemaVersion':1,'visibility':'private' if private else 'public',
          'budgetAccountDeltaUsd':bound['deltaUsd'] if bound else None,
          'budgetObservedAt':bound['observedAt'] if bound else None,'documents':documents})


def folder_for(work, paper):
    return work/'review'/paper


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['packets','export'])
    parser.add_argument('--work',type=Path,default=DEFAULT_WORK)
    parser.add_argument('--paper')
    parser.add_argument('--destination',type=Path,default=HERE/'site')
    parser.add_argument('--private',action='store_true',help='Include local excerpts/images; destination must be inside .local-dev')
    args=parser.parse_args()
    if args.command=='packets':packets(args.work.resolve(),args.paper)
    else:
        if args.private:
            if not args.destination.resolve().is_relative_to(DEFAULT_WORK.parent.resolve()):
                raise ValueError('Private exports must stay inside the ignored .local-dev directory')
            args.destination.mkdir(parents=True,exist_ok=True)
            for filename in ('index.html','app.js','ui.js','pdf-viewer.js','style.css','pdf-viewer.css','favicon.svg'):
                shutil.copy2(HERE/'site'/filename,args.destination/filename)
            shutil.copytree(HERE/'site'/'icons',args.destination/'icons',dirs_exist_ok=True)
            shutil.copytree(HERE/'site'/'vendor',args.destination/'vendor',dirs_exist_ok=True)
        export(args.work.resolve(),args.destination.resolve(),args.private)
