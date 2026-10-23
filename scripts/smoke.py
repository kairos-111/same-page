"""Real-time end-to-end evaluation on a local file; no network model retrieval."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HOME']=str(ROOT/'work/hf-cache')
os.environ['NUMBA_CACHE_DIR']=str(ROOT/'work/numba-cache')

async def main():
    from classroom.runtime import Runtime
    from classroom.session import ClassSession
    parser=argparse.ArgumentParser()
    parser.add_argument('--audio',type=Path,default=ROOT/'work/downloads/samples/jfk.wav')
    parser.add_argument('--model',choices=['turbo','small'],default='turbo')
    parser.add_argument('--fast',action='store_true')
    args=parser.parse_args()
    runtime=Runtime()
    session=ClassSession(runtime,'file',args.model,'公开样例端到端测试',args.audio.resolve(),realtime=not args.fast)
    emit=session.event
    def report(event,persist=True):
        emit(event,persist)
        if event['type']=='segment':print(json.dumps(event,ensure_ascii=False),flush=True)
        elif event['type']=='error':print('ERROR',event,flush=True)
    session.event=report
    session.orchestrator=asyncio.create_task(session.run())
    try:
        await session.orchestrator
        meta=session.store.metadata
        result=dict(session_id=session.id,model=args.model,state=session.state,error=session.error,
                    audio_seconds=meta.get('audio_seconds'),processed_seconds=meta.get('processed_seconds'),
                    first_translation_latency=meta.get('first_translation_latency'),
                    final_translation_latency=meta.get('final_translation_latency'),
                    segments=list(session.segments.values()))
        path=ROOT/'work'/('smoke-'+args.model+'.json')
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print('REPORT',path,flush=True)
        if session.error or not any(s.get('translation') for s in session.segments.values()):raise SystemExit(1)
    finally:await runtime.close()

if __name__=='__main__':asyncio.run(main())
