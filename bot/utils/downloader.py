"""
Téléchargement async + FFmpeg + barre de progression Telegram.
"""
import os
import asyncio
import aiohttp
import aiofiles
from typing import Callable, Optional

from bot.config import Config


class ProgressBar:
    def __init__(self, client, chat_id: int, message_id: int, total_size: int, caption: str = "Téléchargement..."):
        self.client = client
        self.chat_id = chat_id
        self.message_id = message_id
        self.total_size = total_size
        self.downloaded = 0
        self.last_percent = -1
        self.caption = caption

    async def update(self, chunk_size: int):
        self.downloaded += chunk_size
        if self.total_size > 0:
            percent = int(self.downloaded * 100 / self.total_size)
            if percent != self.last_percent and percent % 5 == 0:
                self.last_percent = percent
                try:
                    await self.client.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.message_id,
                        text=f"{self.caption}\n{'█' * (percent // 10)}{'░' * (10 - percent // 10)} {percent}%"
                    )
                except Exception:
                    pass


async def download_file(
    url: str,
    dest_path: str,
    client=None,
    chat_id: Optional[int] = None,
    progress_msg_id: Optional[int] = None,
    caption: str = "Téléchargement..."
) -> str:
    """
    Télécharge un fichier depuis une URL vers dest_path.
    Si client + chat_id + progress_msg_id sont fournis, affiche une barre de progression.
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=600)) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            progress = None
            if client and chat_id and progress_msg_id and total > 0:
                progress = ProgressBar(client, chat_id, progress_msg_id, total, caption)

            async with aiofiles.open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(8192):
                    await f.write(chunk)
                    if progress:
                        await progress.update(len(chunk))
    return dest_path


async def convert_to_480p(input_path: str, output_path: str) -> str:
    """
    Convertit une vidéo en 480p avec FFmpeg.
    Écrase le fichier de sortie s'il existe.
    """
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=-2:480",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg a échoué (code {proc.returncode})")
    return output_path


async def cleanup_files(*paths: str):
    """Supprime les fichiers temporaires."""
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
