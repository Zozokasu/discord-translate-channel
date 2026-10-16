import asyncio

import httpx

LANGUAGES = {"en": "English", "ja": "Japanese"}

SYSTEM_PROMPT = """You translate messages in a Discord chat between Japanese and English speakers.
Translate the user's message into {language}.
- Output only the translation. No explanations, notes or surrounding quotes.
- Keep mentions (<@123>, <@&123>, <#123>), custom emoji (<:name:123>), URLs, code blocks and inline code exactly as they are.
- Keep the tone, line breaks and Markdown formatting of the original.
- If the message is already in {language}, return it unchanged."""


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
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(language=LANGUAGES[target])},
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
