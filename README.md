# SubtitLocar (ローカルAI字幕システム) v0.1

完全ローカル環境で動作する、OBS向けリアルタイム字幕翻訳システムです。  
マイクや出力デバイスの音声を Faster-Whisper で文字起こしし、Ollama (ローカルLLM) で翻訳後、OBSのブラウザソース上にタイプライターエフェクト付きで表示します。

## 必要なソフトウェア

| ソフトウェア | 用途 | インストール先 |
|---|---|---|
| Python 3.10 以上 | バックエンド実行環境 | https://www.python.org/ |
| [Ollama](https://ollama.com/) | ローカルLLMランタイム | https://ollama.com/ |
| OBS Studio | 配信ソフト (ブラウザソース) | https://obsproject.com/ |
| CUDA Toolkit (任意) | GPU推論の高速化 | https://developer.nvidia.com/cuda-downloads |

## セットアップ手順

### 1. Python環境でのセットアップ（ソースコードから実行する場合）

```powershell
# プロジェクトフォルダに移動
cd <SubtitLocarのディレクトリ>
pip install -r requirements.txt
```

### 2. Ollamaのモデルをダウンロード

```powershell
# config.yaml の translation.model に合わせて変更してください
ollama pull qwen2.5:14b
```

### 3. 設定ファイルの編集

`config.yaml` を開き、必要に応じて設定を変更してください。

**特に重要な設定:**
- `audio.mode`: `microphone` / `loopback` / `both` から選択
- `audio.loopback_device_name`: ループバックするデバイス名（部分一致）
- `stt.model`: Whisperのモデルサイズ（VRAMが多いほど大きいモデルを使用可）
- `translation.model`: Ollamaのモデル名

### 4. システムの起動

```powershell
# Ollamaを先に起動 (別ターミナルで)
ollama serve

# 字幕システムを起動 (ソースコードから実行する場合)
cd <SubtitLocarのディレクトリ>
python src/main.py
```
*(※ インストーラー版の場合はスタートメニュー等から直接GUIアプリを起動してください)*

起動時に利用可能なオーディオデバイスの一覧が表示されます。
`config.yaml` の `microphone_name` や `loopback_device_name` に合わせてください。

### 5. OBSのブラウザソース設定

1. OBS Studio を起動
2. ソースパネルで `+` → `ブラウザ` を追加
3. 以下のように設定:

| 項目 | 設定値 |
|---|---|
| ローカルファイル | ✅ チェックを入れる |
| ファイルパス | `<SubtitLocarのディレクトリ>\frontend\subtitle.html` |
| 幅 | `1920` |
| 高さ | `1080` |
| カスタムCSS | (空欄) |
| OBSが非表示の時にソースをシャットダウン | ✅ チェックを入れる |

4. `OK` で保存

## カスタマイズ

`frontend/subtitle.css` の `★ カスタマイズ` と記されたセクションを編集することで、以下を変更できます:

- フォントサイズ・太さ
- 文字色
- 縁取りの太さ・色・ドロップシャドウ
- 字幕の表示位置（下からの距離）

`frontend/subtitle.js` の `CONFIG` オブジェクトで以下を変更できます:

- `TYPEWRITER_INTERVAL_MS`: タイプライターの速度
- `SUBTITLE_DISPLAY_MS`: 字幕の表示時間
- `MAX_LINES`: 同時表示する最大行数

## トラブルシューティング

### OBSに字幕が表示されない
- `python src/main.py` が正常に起動しているか確認
- Ollamaが起動しているか確認 (`ollama serve`)
- config.yaml の `websocket.port` がデフォルト (`8765`) のままか確認

### 音声が認識されない
- `audio.mode` が正しいか確認
- 起動時に表示されるデバイス一覧から正しいデバイス名をコピーして設定

### 翻訳が遅い
- `stt.model` を `medium` や `small` に変更して高速化
- `translation.model` を小さいモデルに変更 (例: `qwen2.5:7b`)
- `stt.device` が `cuda` になっているか確認 (GPUを使用しているか)

## ディレクトリ構成

```
Locally_Translated_Subtitle_Project/
├── src/
│   ├── main.py               # エントリーポイント
│   ├── audio_capture.py      # 音声キャプチャ
│   ├── stt_engine.py         # Whisper STT
│   ├── translator.py         # Ollama翻訳
│   └── websocket_server.py   # WebSocketサーバー
├── frontend/
│   ├── subtitle.html         # OBSブラウザソース用
│   ├── subtitle.css          # スタイル (カスタマイズ用)
│   └── subtitle.js           # エフェクト制御
├── config.yaml               # ユーザー設定
├── requirements.txt          # Python依存関係
└── README.md                 # このファイル
```
