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
        self.proc            = None
        self.reader_task     = None

    async def start_session(self, system_prompt: str = None):
        logger.info("Nova Sonic: session started")
        self.is_active      = True
        self._is_processing = False

    async def start_audio_input(self):
        if not self.is_active:
            await self.start_session()
        
        logger.info("Nova Sonic: starting real-time STT process")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                "node", _STT_SCRIPT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.reader_task = asyncio.create_task(self._read_stdout())
        except Exception as e:
            logger.error(f"Nova Sonic: failed to start STT process: {e}")
            self.is_active = False

    async def _read_stdout(self):
        """Background task to read transcripts from the Node process."""
        try:
            while self.proc and self.proc.stdout:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                
                decoded = line.decode().strip()
                if decoded.startswith("TRANSCRIPT_PARTIAL:"):
                    partial = decoded[len("TRANSCRIPT_PARTIAL:"):].strip()
                    if partial and self.on_text_chunk:
                        # We treat partials as is_user=True but maybe we need a separate flag?
                        # For now, let's just pass it through.
                        res = self.on_text_chunk(partial, is_user=True)
                        if asyncio.iscoroutine(res): await res
                elif decoded.startswith("TRANSCRIPT:"):
                    # We already have the fragments in user_transcripts via PARTIAL
                    # and handle_finished will join them.
                    pass
            
            # Read stderr for debug
            if self.proc and self.proc.stderr:
                err_data = await self.proc.stderr.read()
                if err_data:
                    for l in err_data.decode().splitlines():
                        logger.debug(f"STT: {l}")
        except Exception as e:
            logger.error(f"Nova Sonic: error in stdout reader: {e}")

    async def send_audio_chunk(self, base64_audio: str):
        if not self.is_active or not self.proc or not self.proc.stdin:
            return
        try:
            # We must send base64 per chunk as the script uses readline
            self.proc.stdin.write(f"{base64_audio}\n".encode())
            await self.proc.stdin.drain()
        except Exception as e:
            logger.error(f"Nova Sonic: error writing to STT stdin: {e}")

    async def end_audio_input(self):
        if not self.is_active or self._is_processing:
            return

        self._is_processing = True
        logger.info("Nova Sonic: ending audio input")

        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.close()
                await self.proc.stdin.wait_closed()
            except: pass

        if self.reader_task:
            await self.reader_task

        if self.proc:
            try:
                await self.proc.wait()
            except: pass
            self.proc = None

        if self.on_finished:
            await self.on_finished()
        
        self._is_processing = False
        self.is_active = False

    async def end_session(self):
        self.is_active      = False
        self._is_processing = False
        if self.proc:
            try:
                self.proc.terminate()
                await self.proc.wait()
            except: pass
            self.proc = None
        if self.reader_task:
            self.reader_task.cancel()
            self.reader_task = None
