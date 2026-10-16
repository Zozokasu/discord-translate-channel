# discord-translate

日本語のチャンネルと英語のチャンネルを1組にして、片方への発言を LLM で翻訳し、もう片方へ Webhook で投稿する Discord Bot です。1サーバーでの利用を前提にしています。

## 準備

1. Discord Developer Portal で Bot を作り、**Message Content Intent** をオンにする。
2. 両方のチャンネルで、Bot に次の権限を与える：メッセージを見る・送る、メッセージ履歴を読む、Webhook の管理、リアクションの追加。
3. `.env.example` を `.env` にコピーして値を入れる。

| キー | 必須 | 既定値 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ○ | |
| `JA_CHANNEL_ID` | ○ | |
| `EN_CHANNEL_ID` | ○ | |
| `LLM_API_KEY` | ○ | |
| `LLM_MODEL` | ○ | |
| `LLM_BASE_URL` | | `https://api.openai.com/v1` |
| `LLM_TIMEOUT_SECONDS` | | `30` |
| `LLM_MAX_RETRIES` | | `2` |

## 起動と停止

```
uv sync
uv run main.py
```

止めるときは `Ctrl+C`。

## 動き

- 発言を翻訳し、投稿者の名前とアイコンで相手のチャンネルに投稿する。返信先へのリンクと添付ファイルの URL も付ける。
- 原文の編集・削除を訳文に反映する。
- 翻訳に失敗すると原文に ⚠️ を付ける。
- 原文と訳文の対応はメモリにだけ持つため、再起動より前の発言の編集・削除は反映されない。
- 発言の本文は `LLM_BASE_URL` の LLM ホストに送られる。
