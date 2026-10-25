"""Start the application using local model files only."""
from pathlib import Path
import os
import sys
import shutil
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
from classroom.paths import WORK, OUTPUTS, API_PORT
WORK.mkdir(parents=True,exist_ok=True)
OUTPUTS.mkdir(parents=True,exist_ok=True)
terms=OUTPUTS/'课程术语.csv'
template=root/'outputs'/'课程术语.csv'
if not template.exists():template=root/'outputs'/'terms-template.csv'  # Windows build: ASCII file names only
if not terms.exists() and template.exists():shutil.copyfile(template,terms)  # data only; CopyFile2 fails on some Windows setups
os.environ['HF_HOME']=str(WORK/'hf-cache')
os.environ['NUMBA_CACHE_DIR']=str(WORK/'numba-cache')
os.environ['MPLCONFIGDIR']=str(WORK/'matplotlib-cache')
os.environ['XDG_CACHE_HOME']=str(WORK/'cache')
if sys.platform!='win32':
    # macOS decodes imported files with the bundled ffmpeg; Windows uses PyAV.
    if (root/'bundle.json').exists():os.environ.pop('IMAGEIO_FFMPEG_EXE',None)
    import imageio_ffmpeg
    ffmpeg=Path(imageio_ffmpeg.get_ffmpeg_exe())
    bindir=WORK/'bin';bindir.mkdir(parents=True,exist_ok=True)
    link=bindir/'ffmpeg'
    if link.is_symlink() and Path(os.readlink(link)) != ffmpeg:
        link.unlink() # Replace only this app-owned cache link after moving/upgrading the app.
    if not link.exists() and not link.is_symlink():link.symlink_to(ffmpeg)
    os.environ['PATH']=str(bindir)+os.pathsep+os.environ.get('PATH','')
import uvicorn
uvicorn.run('classroom.app:app',host='127.0.0.1',port=API_PORT,log_level='info')
