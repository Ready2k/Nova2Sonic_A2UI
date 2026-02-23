import os
import asyncio
import base64
import logging
import traceback

logger = logging.getLogger(__name__)

# Resolve the path to nova_sonic_stt.mjs once at import time.
# nova_sonic.py lives at server/app/nova_sonic.py → server/ is two levels up.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STT_SCRIPT = os.path.join(_SERVER_DIR, "nova_sonic_stt.mjs")


class NovaSonicSession:
    def __init__(self, on_audio_chunk, on_text_chunk, on_finished):
        self.on_audio_chunk = on_audio_chunk
        self.on_text_chunk  = on_text_chunk
        self.on_finished    = on_finished

        self.is_active       = False
        self._is_processing  = False
        self.audio_buffer    = []   # raw base64 chunks from client

    async def start_session(self, system_prompt: str = None):
        logger.info("Nova Sonic: session started")
        self.audio_buffer   = []
        self.is_active      = True
        self._is_processing = False

    async def start_audio_input(self):
        logger.info("Nova Sonic: ready for audio")
        self.audio_buffer = []

    async def send_audio_chunk(self, base64_audio: str):
        if not self.is_active:
            return
        self.audio_buffer.append(base64_audio)

    async def end_audio_input(self):
        if not self.is_active or self._is_processing:
            return

        self._is_processing = True
        chunk_count = len(self.audio_buffer)
        logger.info(f"Nova Sonic: ending audio input ({chunk_count} chunks)")

        # --- Fix base64 concatenation ---
        # Each client chunk is independently base64-encoded.  Naively joining
        # the strings produces invalid base64 because of internal padding chars.
        # Decode each chunk to bytes, concatenate, then re-encode as one block.
        audio_bytes = b""
        for chunk in self.audio_buffer:
            try:
                audio_bytes += base64.b64decode(chunk)
            except Exception as e:
                logger.warning(f"Nova Sonic: skipping malformed audio chunk: {e}")
        self.audio_buffer = []

        if not audio_bytes:
            logger.warning("Nova Sonic: no valid audio data — skipping STT")
            if self.on_finished:
                await self.on_finished()
            self._is_processing = False
            self.is_active = False
            return

        full_audio_b64 = base64.b64encode(audio_bytes).decode()
        logger.info(f"Nova Sonic: total audio {len(audio_bytes)} bytes → STT script")

        try:
            proc = await asyncio.create_subprocess_exec(
                "node", _STT_SCRIPT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=full_audio_b64.encode()),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                logger.error("Nova Sonic: STT script timed out after 30s")
                proc.kill()
                await proc.wait()
                stdout, stderr = b"", b""

            if stderr:
                # STT debug lines go to stderr — log them at DEBUG level
                for line in stderr.decode().splitlines():
                    logger.debug(f"STT: {line}")

            transcript = ""
            for line in stdout.decode().splitlines():
                if line.startswith("TRANSCRIPT:"):
                    transcript = line[len("TRANSCRIPT:"):].strip()
                    break

            logger.info(f"Nova Sonic: transcript='{transcript}'")

            if transcript and self.on_text_chunk:
                result = self.on_text_chunk(transcript, True)
                if asyncio.iscoroutine(result):
                    await result

        except Exception as e:
            logger.error(f"Nova Sonic: STT error: {e}")
            traceback.print_exc()
        finally:
            if self.on_finished:
                await self.on_finished()
            self._is_processing = False
            self.is_active = False

    async def end_session(self):
        self.is_active      = False
        self._is_processing = False
        self.audio_buffer   = []
