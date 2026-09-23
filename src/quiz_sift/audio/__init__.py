"""音声化(R-7 / ADR-004)。

`synthesize` がトラックの台本をセグメントごとに `ports.tts.Tts` で読み上げ、`Pause` は自前の無音 PCM
として挟んで連結し、`codec`(ffmpeg)で mp3 に書き出す。書き出した mp3 はデコードし直して
`silence.detect_structure` で無音構造を判定する。ファイル名は `naming`、再生リストは `playlist`、
言語とボイスの一致は `voices` が持つ。
"""
