"""Reusable permission-check decorators for command handlers."""

from __future__ import annotations

import functools
from typing import Callable

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.enums import ChatMemberStatus, ChatType

from config import Config


def admin_required(func: Callable):
    """Allow only group admins (+ owner / creator) to run the handler."""

    @functools.wraps(func)
    async def wrapper(client: Client, message: Message):
        # Private chats -- allow
        if message.chat.type == ChatType.PRIVATE:
            return await func(client, message)

        user_id = message.from_user.id if message.from_user else 0

        # Sudo / owner bypass
        if user_id in Config.SUDO_USERS or user_id == Config.OWNER_ID:
            return await func(client, message)

        try:
            member = await client.get_chat_member(message.chat.id, user_id)
            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            ):
                await message.reply_text(
                    "❌ শুধুমাত্র অ্যাডমিনরা এই কমান্ড ব্যবহার করতে পারবে।\n"
                    "Only admins can use this command."
                )
                return
        except Exception:
            await message.reply_text(
                "❌ তোমার অনুমতি যাচাই করা যায়নি। / Could not verify permissions."
            )
            return

        return await func(client, message)

    return wrapper


def sudo_required(func: Callable):
    """Allow only sudo users / bot owner to run the handler."""

    @functools.wraps(func)
    async def wrapper(client: Client, message: Message):
        user_id = message.from_user.id if message.from_user else 0

        if user_id not in Config.SUDO_USERS and user_id != Config.OWNER_ID:
            await message.reply_text(
                "❌ এই কমান্ড শুধুমাত্র sudo ইউজারদের জন্য।\n"
                "This command is for sudo users only."
            )
            return

        return await func(client, message)

    return wrapper
