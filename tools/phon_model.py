"""mp3 -> фонемы IPA с временем: локальная модель facebook/wav2vec2-xlsr-53-espeak-cv-ft
(~1.3 ГБ в кэше HuggingFace, GPU если есть), жадный CTC-декод. Токенизатор модели
требует phonemizer только для текста->фонемы; нам нужен обратный путь, поэтому
словарь берётся из vocab.json напрямую. Используется tools/pronunciation_check.py."""
import json, subprocess, sys
import numpy as np
import torch
from transformers import AutoModelForCTC, AutoFeatureExtractor
from huggingface_hub import hf_hub_download

M = 'facebook/wav2vec2-xlsr-53-espeak-cv-ft'
_dev = 'cuda' if torch.cuda.is_available() else 'cpu'
_fe = AutoFeatureExtractor.from_pretrained(M)
_mod = AutoModelForCTC.from_pretrained(M).to(_dev).eval()
_vocab = {i: t for t, i in json.load(open(hf_hub_download(M, 'vocab.json'), encoding='utf-8')).items()}
FRAME = 0.02  # секунды на кадр CTC


def load_audio(path_or_bytes):
    args = ['ffmpeg', '-v', 'quiet', '-i', 'pipe:0' if isinstance(path_or_bytes, bytes) else path_or_bytes,
            '-f', 's16le', '-ac', '1', '-ar', '16000', 'pipe:1']
    out = subprocess.run(args, input=path_or_bytes if isinstance(path_or_bytes, bytes) else None,
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


@torch.no_grad()
def phonemes(audio):
    x = _fe(audio, sampling_rate=16000, return_tensors='pt').input_values.to(_dev)
    ids = _mod(x).logits.argmax(-1)[0].cpu().numpy()
    out, prev = [], None
    for k, i in enumerate(ids):
        if i != prev and _vocab.get(int(i)) not in ('<pad>', '<s>', '</s>', None):
            out.append((_vocab[int(i)], round(k * FRAME, 3)))
        prev = i
    return out


def in_window(ph, start, end, pad=0.04):
    return ''.join(p for p, t in ph if start - pad <= t < end + pad)


if __name__ == '__main__':
    a = load_audio(sys.argv[1])
    ph = phonemes(a)
    print(' '.join(f'{p}@{t}' for p, t in ph))
