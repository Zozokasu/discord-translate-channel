import asyncio

import httpx

LANGUAGES = {"ja": "Japanese", "en": "English", "ko": "Korean"}
LANGUAGES_JA = {"ja": "日本語", "en": "英語", "ko": "韓国語"}

# SYSTEM_PROMPT = """You translate messages in a Discord chat between Japanese, English and Korean speakers.
# Translate the user's message into {language}.
# - Output only the translation. No explanations, notes or surrounding quotes.
# - Keep mentions (<@123>, <@&123>, <#123>), custom emoji (<:name:123>), URLs, code blocks and inline code exactly as they are.
# - Keep the tone, line breaks and Markdown formatting of the original.
# - If the message is already in {language}, return it unchanged."""

SYSTEM_PROMPT = """あなたは、日本語・英語・韓国語の話者が集まる Discord のチャットで発言を訳す翻訳者です。
ユーザーから渡された発言を{language}に翻訳してください。

# 出力
- 訳文だけを出力してください。説明・注釈・前置き・引用符は付けないでください。
- 発言の内容はすべて訳してください。文や行を省いたり、要約したり、まとめたりしないでください。
- 原文にない内容を足さないでください。
- 改行の位置と数、Markdown の書式（太字・箇条書き・引用・見出しなど）は原文のまま保ってください。

# 訳し方
- 直訳ではなく、{language}を母語とする人がチャットで自然に書く表現にしてください。丁寧さ・くだけ具合・感情の強さも原文に合わせてください。
- 「やったー」「えー」「草」「w」などの感嘆詞・相づち・ネットスラングは、音を書き写したり固有名詞と取り違えたりせず、意味と気持ちが伝わる表現にしてください。たとえば「やったー」は英語なら "Yay!"、韓国語なら "야호!" です。
- 顔文字・絵文字・記号はそのまま残してください。
- 人名・ユーザー名・ゲームやサービスの名前などの固有名詞は、{language}で広く使われている表記があればそれを使い、なければ原文のまま残してください。

# 変えずに残すもの
- メンション（<@123>、<@&123>、<#123>）、カスタム絵文字（<:name:123>、<a:name:123>）、URL、コードブロック、インラインコードは、一文字も変えずに残してください。

# 訳さずに返す場合
- 発言がすでに{language}で書かれているときは、手を加えずにそのまま返してください。
- 記号・絵文字・URL だけの発言は、そのまま返してください。"""

CHANNEL_NAME_PROMPT = """Translate the given Discord channel name into a short {language} channel name.
Output only the name, with words separated by hyphens and Latin letters in lowercase. No explanations."""


class TranslationError(Exception):
    pass


class Translator:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def translate(self, text: str, target: str) -> str:
        return await self._complete(SYSTEM_PROMPT.format(language=LANGUAGES_JA[target]), text)

    async def channel_name(self, name: str, target: str) -> str:
        return await self._complete(CHANNEL_NAME_PROMPT.format(language=LANGUAGES[target]), name)

    async def _complete(self, system_prompt: str, text: str) -> str:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(2**attempt)
            try:
                response = await self._client.post(self._url, json=body)
            except httpx.TransportError as e:
                last_error = e
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = TranslationError(f"LLM returned HTTP {response.status_code}")
                continue
            if response.is_error:
                raise TranslationError(
                    f"LLM returned HTTP {response.status_code}: {response.text[:300]}"
                )
            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as e:
                raise TranslationError("unexpected LLM response format") from e
            if not content or not content.strip():
                raise TranslationError("LLM returned an empty translation")
            return content.strip()
        raise TranslationError(f"LLM request failed: {last_error}") from last_error

    async def aclose(self) -> None:
        await self._client.aclose()
