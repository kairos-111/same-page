"""Start the application using local model files only."""
from pathlib import Path
import os
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
os.environ['HF_HOME']=str(root/'work'/'hf-cache')
os.environ['NUMBA_CACHE_DIR']=str(root/'work'/'numba-cache')
import imageio_ffmpeg
ffmpeg=Path(imageio_ffmpeg.get_ffmpeg_exe())
bindir=root/'work'/'bin';bindir.mkdir(parents=True,exist_ok=True)
link=bindir/'ffmpeg'
if not link.exists():link.symlink_to(ffmpeg)
os.environ['PATH']=str(bindir)+os.pathsep+os.environ.get('PATH','')
import uvicorn
uvicorn.run('classroom.app:app',host='127.0.0.1',port=8767,log_level='info')
