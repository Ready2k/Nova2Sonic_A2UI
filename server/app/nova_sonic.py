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
            self.stderr_task = asyncio.create_task(self._read_stderr())
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
                        res = self.on_text_chunk(partial, is_user=True, is_final=False)
                        if asyncio.iscoroutine(res): await res
                elif decoded.startswith("TRANSCRIPT:"):
                    final = decoded[len("TRANSCRIPT:"):].strip()
                    if final and self.on_text_chunk:
                        res = self.on_text_chunk(final, is_user=True, is_final=True)
                        if asyncio.iscoroutine(res): await res
            
            logger.info("Nova Sonic: STT process stdout closed")
        except Exception as e:
            logger.error(f"Nova Sonic: error in stdout reader: {e}")

    async def _read_stderr(self):
        """Task to stream stderr to logs in real-time."""
        try:
            while self.proc and self.proc.stderr:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                logger.debug(f"Nova Sonic STT (stderr): {line.decode().strip()}")
        except Exception as e:
            logger.error(f"Nova Sonic: error in stderr reader: {e}")

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
                logger.info("Nova Sonic: closing STT stdin")
                self.proc.stdin.close()
                await self.proc.stdin.wait_closed()
            except Exception as e:
                logger.warning(f"Nova Sonic: error closing stdin: {e}")

        if self.reader_task:
            try:
                # Wait up to 5 seconds for the process to finish after stdin close
                await asyncio.wait_for(self.reader_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Nova Sonic: STT reader task timed out, force terminating")
                if self.proc:
                    try:
                        self.proc.terminate()
                    except: pass
            except Exception as e:
                logger.error(f"Nova Sonic: error waiting for reader: {e}")

        if self.proc:
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2.0)
            except Exception:
                if self.proc:
                    try: self.proc.kill()
                    except: pass
            self.proc = None

        if self.on_finished:
            logger.info("Nova Sonic: triggering on_finished callback")
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
