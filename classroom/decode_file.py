"""Decode any audio/video file to 16 kHz mono s16le PCM on stdout (PyAV, LGPL FFmpeg).

Used on Windows instead of an ffmpeg executable: `python decode_file.py <path>`.
Errors are reported as one JSON line on stderr, like the capture helpers.
"""
import json
import sys


def status(message, kind='error'):
    sys.stderr.write(json.dumps(dict(type=kind, message=message), ensure_ascii=False) + '\n')
    sys.stderr.flush()


def decode(path, out):
    import av
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == 'audio'), None)
        if stream is None:
            raise ValueError('文件中没有音轨')
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        written = 0

        def emit(frames):
            nonlocal written
            for frame in frames:
                data = frame.to_ndarray().astype('<i2', copy=False).tobytes()
                out.write(data)
                written += len(data)

        for frame in container.decode(stream):
            emit(resampler.resample(frame))
        emit(resampler.resample(None))
        out.flush()
        return written


def main(argv):
    if len(argv) < 2:
        status('缺少文件路径')
        return 2
    try:
        if not decode(argv[1], sys.stdout.buffer):
            status('文件中没有可识别的声音')
            return 1
    except BrokenPipeError:
        return 0
    except Exception as exc:
        status('无法读取导入的文件：' + str(exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
