import asyncio
import logging
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass

import discord
from dotenv import load_dotenv

from translate import TranslationError, Translator

WEBHOOK_NAME = "JA-EN Translator"
MAX_LENGTH = 1900
MAX_TRACKED = 5000
REPLY_LABEL = {"en": "Reply", "ja": "返信"}
NO_MENTIONS = discord.AllowedMentions.none()

log = logging.getLogger("translate-bot")


@dataclass
class Mirror:
    source_content: str
    target_channel_id: int
    target_ids: list[int]


def split_message(text: str, limit: int = MAX_LENGTH) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n ")
    if text:
        chunks.append(text)
    return chunks


def webhook_username(member: discord.abc.User) -> str:
    name = member.display_name.strip()[:80]
    lowered = name.lower()
    if not name or "discord" in lowered or "clyde" in lowered:
        return member.name[:80] if member.name else "User"
    return name


class TranslateBot(discord.Client):
    def __init__(self, ja_channel_id: int, en_channel_id: int, translator: Translator) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=NO_MENTIONS)
        self.translator = translator
        self.targets = {ja_channel_id: (en_channel_id, "en"), en_channel_id: (ja_channel_id, "ja")}
        self.webhooks: dict[int, discord.Webhook] = {}
        self.locks = {channel_id: asyncio.Lock() for channel_id in self.targets}
        self.mirrors: OrderedDict[int, Mirror] = OrderedDict()
        self.origins: dict[int, int] = {}

    async def on_ready(self) -> None:
        for channel_id in self.targets:
            channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError(f"channel {channel_id} is not a text channel")
            hooks = await channel.webhooks()
            hook = next(
                (h for h in hooks if h.name == WEBHOOK_NAME and h.user == self.user), None
            )
            self.webhooks[channel_id] = hook or await channel.create_webhook(name=WEBHOOK_NAME)
        log.info("ready as %s", self.user)

    async def close(self) -> None:
        await self.translator.aclose()
        await super().close()

    def is_target(self, message: discord.Message) -> bool:
        return (
            message.channel.id in self.targets
            and message.channel.id in self.webhooks
            and not message.author.bot
            and message.webhook_id is None
            and message.type in (discord.MessageType.default, discord.MessageType.reply)
        )

    def reply_link(self, message: discord.Message, target_channel_id: int, lang: str) -> str:
        if message.reference is None or message.reference.message_id is None:
            return ""
        replied_id = message.reference.message_id
        if replied_id in self.mirrors:
            mirror = self.mirrors[replied_id]
            channel_id, counterpart_id = mirror.target_channel_id, mirror.target_ids[0]
        elif replied_id in self.origins:
            channel_id, counterpart_id = target_channel_id, self.origins[replied_id]
        else:
            return ""
        url = f"https://discord.com/channels/{message.guild.id}/{channel_id}/{counterpart_id}"
        return f"-# ↪ [{REPLY_LABEL[lang]}]({url})\n"

    async def build_chunks(self, message: discord.Message, target_channel_id: int, lang: str) -> list[str]:
        parts = []
        if message.content.strip():
            parts.append(await self.translator.translate(message.content, lang))
        parts.extend(attachment.url for attachment in message.attachments)
        if not parts:
            return []
        return split_message(self.reply_link(message, target_channel_id, lang) + "\n".join(parts))

    async def send_chunks(self, message: discord.Message, target_channel_id: int, chunks: list[str]) -> list[int]:
        webhook = self.webhooks[target_channel_id]
        ids = []
        for chunk in chunks:
            sent = await webhook.send(
                chunk,
                username=webhook_username(message.author),
                avatar_url=message.author.display_avatar.url,
                allowed_mentions=NO_MENTIONS,
                wait=True,
            )
            ids.append(sent.id)
        return ids

    def remember(self, source_id: int, mirror: Mirror) -> None:
        self.mirrors[source_id] = mirror
        self.mirrors.move_to_end(source_id)
        for target_id in mirror.target_ids:
            self.origins[target_id] = source_id
        while len(self.mirrors) > MAX_TRACKED:
            _, old = self.mirrors.popitem(last=False)
            for target_id in old.target_ids:
                self.origins.pop(target_id, None)

    def forget(self, source_id: int) -> Mirror | None:
        mirror = self.mirrors.pop(source_id, None)
        if mirror:
            for target_id in mirror.target_ids:
                self.origins.pop(target_id, None)
        return mirror

    async def mark_failed(self, message: discord.Message) -> None:
        try:
            await message.add_reaction("⚠️")
        except discord.HTTPException:
            pass

    async def on_message(self, message: discord.Message) -> None:
        if not self.is_target(message):
            return
        target_channel_id, lang = self.targets[message.channel.id]
        async with self.locks[message.channel.id]:
            try:
                chunks = await self.build_chunks(message, target_channel_id, lang)
                if not chunks:
                    return
                ids = await self.send_chunks(message, target_channel_id, chunks)
            except (TranslationError, discord.HTTPException):
                log.exception("failed to translate message %s", message.id)
                await self.mark_failed(message)
                return
            self.remember(message.id, Mirror(message.content, target_channel_id, ids))

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.message_id not in self.mirrors or payload.channel_id not in self.targets:
            return
        target_channel_id, lang = self.targets[payload.channel_id]
        async with self.locks[payload.channel_id]:
            mirror = self.mirrors.get(payload.message_id)
            if mirror is None:
                return
            try:
                channel = self.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                if message.content == mirror.source_content:
                    return
                chunks = await self.build_chunks(message, target_channel_id, lang)
                webhook = self.webhooks[target_channel_id]
                if len(chunks) == len(mirror.target_ids):
                    for target_id, chunk in zip(mirror.target_ids, chunks):
                        await webhook.edit_message(target_id, content=chunk, allowed_mentions=NO_MENTIONS)
                    ids = mirror.target_ids
                else:
                    for target_id in mirror.target_ids:
                        await webhook.delete_message(target_id)
                    self.forget(payload.message_id)
                    ids = await self.send_chunks(message, target_channel_id, chunks) if chunks else []
            except (TranslationError, discord.HTTPException):
                log.exception("failed to update translation of message %s", payload.message_id)
                return
            self.forget(payload.message_id)
            if ids:
                self.remember(payload.message_id, Mirror(message.content, target_channel_id, ids))

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self.delete_mirror(payload.message_id)

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        for message_id in payload.message_ids:
            await self.delete_mirror(message_id)

    async def delete_mirror(self, source_id: int) -> None:
        mirror = self.forget(source_id)
        if mirror is None:
            return
        webhook = self.webhooks[mirror.target_channel_id]
        for target_id in mirror.target_ids:
            try:
                await webhook.delete_message(target_id)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.exception("failed to delete translated message %s", target_id)


def main() -> None:
    load_dotenv()
    required = ["DISCORD_BOT_TOKEN", "JA_CHANNEL_ID", "EN_CHANNEL_ID", "LLM_API_KEY", "LLM_MODEL"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        sys.exit(f"missing settings in .env: {', '.join(missing)}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    translator = Translator(
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL"],
        timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )
    bot = TranslateBot(int(os.environ["JA_CHANNEL_ID"]), int(os.environ["EN_CHANNEL_ID"]), translator)
    bot.run(os.environ["DISCORD_BOT_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
