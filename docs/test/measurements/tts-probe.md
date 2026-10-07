# 実測ログ: TTS プローブ(実 CosyVoice、torch 2.10)

フル処理の前のリハーサル(T-069 / C-007 R-7、D-215)。`tests/fixtures/tts-probe/script.json` の合成の台本1本
(問いかけ → 間 → 答え(2要素)→ 識別ポイント。教材を含まない)を、製品の audio 段と同じ経路
(`audio.synthesize.synthesize_tracks`: 合成 → 間の挿入 → mp3 への符号化 → `decode_mp3` → `has_pause_structure`)
でホストの実 CosyVoice に通した。LLM は呼んでいない。`tests/fixtures/tts-probe/probe.py` が書き、
`tests/measurements/test_tts_probe_logs.py` が機械判定する。

`runs` は書き出した mp3 を復号し直して `audio.silence.detect_structure` に掛けた区間列。

## 記録

```yaml
probe: tests/fixtures/tts-probe/probe.py
probe_sha256: 18ac1fbcce4d38ec793ff7d46f2a29273d207955ea450c77fbad5462c831415f
started_at: '2026-10-07T16:13:20+00:00'
commit: 9da500fb92b76c0cb1b848ae8e0298ca47109700
python: 3.13.5
torch_version: 2.10.0+cu128
cuda_available: true
cosyvoice_root: /home/koich/CosyVoice
cosyvoice_commit: 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
cosyvoice_dirty: false
model_dir: /home/koich/CosyVoice/pretrained_models/CosyVoice-300M-SFT
voice: 日语男
script_sha256: 0f5312bfa16c284bdde15d6db9b8b41f02cd140461e509bf49e31da8566e0fd9
segments:
- Prompt
- Pause
- Answer
- Discrimination
tts_calls: 3
inserted_pause_seconds: 3.3
threshold_seconds: 3.0
sample_rate: 22050
mp3: /tmp/tts-probe/001_tts-probe_c001.mp3
mp3_sha256: 10745b6b5b0726c8dcd1ec1597598f3bb474b7b275c6b42e11984d1bf856dcd5
track_seconds: 15.758
runs:
- voiced: false
  start: 0.0
  seconds: 0.379
- voiced: true
  start: 0.379
  seconds: 1.098
- voiced: false
  start: 1.477
  seconds: 0.359
- voiced: true
  start: 1.836
  seconds: 1.756
- voiced: false
  start: 3.592
  seconds: 3.941
- voiced: true
  start: 7.533
  seconds: 2.963
- voiced: false
  start: 10.496
  seconds: 0.579
- voiced: true
  start: 11.075
  seconds: 4.683
has_pause_structure: true
```

## 結論

無音判定: 真
