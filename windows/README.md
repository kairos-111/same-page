# Windows build

Built entirely on the Mac; no Windows machine is needed to produce the installer.

| Piece | Windows | macOS |
|---|---|---|
| Recognition | faster-whisper / CTranslate2, CPU int8 (`classroom/ct2_asr.py`) | MLX |
| VAD | Silero ONNX via numpy (`NumpyVAD`) | same model via torch wrapper |
| Streaming | vendored LocalAgreement (`classroom/wlk/`, avoids torch) | whisperlivekit package |
| Translation | llama.cpp `llama-vulkan`, falling back to `llama-cpu` | llama.cpp Metal |
| Audio capture | `classroom/capture_windows.py` (soundcard / WASAPI, loopback for system audio) | Swift helper |
| File import | `classroom/decode_file.py` (PyAV, LGPL FFmpeg) | ffmpeg |
| Window | `windows/launcher.pyw`: engine in a kill-on-close job object, Edge `--app` window | Tauri |
| Installer | NSIS, per user, `%LOCALAPPDATA%\Programs\Same Page` | — |

The backend switch is `classroom/system.py` (`SAMEPAGE_ASR_BACKEND=ct2` exercises the
Windows recognition path on a Mac; `TINGKE_MODELS` points at another model folder).

## Inputs (once, into `work/win/`)

- `dl/python.tar.gz` — python-build-standalone `cpython-3.12.15+20261021-x86_64-pc-windows-msvc-install_only`
- `dl/llama-b11669-bin-win-vulkan-x64.zip`, `dl/llama-b11669-bin-win-cpu-x64.zip` — llama.cpp release b11669
- `models/whisper-small-ct2/` — `Systran/faster-whisper-small` (config.json, model.bin, tokenizer.json, vocabulary.txt)
- Docker images: `samepage-nsis` (Debian + nsis), optionally `samepage-wine10` (WineHQ 10) for smoke tests

## Build

```
work/runtime/python/bin/python3.12 windows/build_windows.py
```

Produces `work/win/stage/` and `work/win/out/SamePage-<version>-windows-x64-setup.exe` (+ `.sha256`).
Model checksums are verified; the build fails if the installer would exceed NSIS's 2 GB limit.

## Testing without a Windows PC

- `SAMEPAGE_ASR_BACKEND=ct2 TINGKE_MODELS=<folder with whisper-small-ct2 + hy-mt>` runs the
  Windows recognition path on the Mac (tests/test_windows_build.py covers it with a fixture).
- Wine 10 (`samepage-wine10b` image, WineHQ 10.0 pinned; Wine 11 aborts under emulation):
  verified the installer (`/S` silent install, file list and hashes), the Windows Python,
  the engine web layer, and the launcher lifecycle with `windows/fake_window.py`
  (`SAMEPAGE_BROWSER_CMD`). Set `LANG=C.UTF-8` or Wine cannot create Chinese file names.
- Wine limits on this Mac: NumPy imports `ucrtbase.crealf` (missing in Wine, present in
  Windows 10/11), and Rosetta crashes Wine on new threads ("invalid gdt selector"), so the
  model/recognition path and audio capture can only be checked on a real PC.
