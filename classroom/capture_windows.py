"""Windows audio capture helper: 16 kHz mono s16le PCM on stdout, JSON status lines on stderr.

Same protocol as the macOS Swift helper: `python capture_windows.py microphone|system`.
WASAPI converts the device format to 16 kHz mono for us (AUTOCONVERTPCM), and the
soundcard library fills silent periods of loopback capture with zeros, so the
timeline stays continuous.
"""
import json
import sys
import warnings

BLOCK = 1600  # 100 ms


def status(message, kind='status'):
    sys.stderr.write(json.dumps(dict(type=kind, message=message), ensure_ascii=False) + '\n')
    sys.stderr.flush()


def explain(exc, mode):
    text = str(exc)
    if '0x80070005' in text.lower() or 'access' in text.lower() and 'denied' in text.lower():
        return ('Windows 隐私设置阻止了麦克风：请打开“设置 → 隐私和安全性 → 麦克风”，'
                '允许“桌面应用访问你的麦克风”，然后重新开始。')
    if mode == 'microphone':
        return '无法打开麦克风：' + text
    return '无法获取电脑声音：' + text


def main(argv):
    mode = argv[1] if len(argv) > 1 else ''
    if mode not in ('microphone', 'system'):
        status('未知音源', 'error')
        return 2
    warnings.simplefilter('ignore')
    try:
        import numpy as np
        import soundcard
    except Exception as exc:  # pragma: no cover - depends on the Windows install
        status('音频组件加载失败：' + str(exc), 'error')
        return 2
    try:
        if mode == 'microphone':
            device = soundcard.default_microphone()
            if device is None:
                status('没有找到可用的麦克风，请连接或启用麦克风后重试', 'error')
                return 4
        else:
            speaker = soundcard.default_speaker()
            if speaker is None:
                status('没有找到可用的扬声器或耳机，无法获取电脑声音', 'error')
                return 4
            device = soundcard.get_microphone(id=str(speaker.id), include_loopback=True)
        recorder = device.recorder(samplerate=16000, channels=1, blocksize=BLOCK)
        recorder.__enter__()
    except Exception as exc:
        status(explain(exc, mode), 'error')
        return 3
    status('麦克风采集已开始' if mode == 'microphone' else '电脑声音采集已开始：' + device.name, 'ready')
    out = sys.stdout.buffer
    try:
        while True:
            data = recorder.record(numframes=BLOCK)
            samples = np.clip(data[:, 0], -1.0, 1.0)
            out.write((samples * 32767).astype('<i2').tobytes())
            out.flush()
    except (BrokenPipeError, OSError, KeyboardInterrupt):
        return 0
    except Exception as exc:
        status(explain(exc, mode), 'error')
        return 2
    finally:
        try:
            recorder.__exit__(None, None, None)
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main(sys.argv))
