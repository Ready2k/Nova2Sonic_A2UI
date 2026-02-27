import os
import asyncio
import logging

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
        self.stderr_task     = None
        self._system_prompt  = ''
        self._transcript_ready = asyncio.Event()   # set when TRANSCRIPT: arrives
        self._ready_event      = asyncio.Event()   # set when READY arrives
        self._bedrock_done     = asyncio.Event()   # set when Bedrock's promptEnd arrives
        self._bedrock_done.set()                   # first turn has no prior prompt to wait for
        self._ignore_next_transcript = False       # True after interrupt() — suppresses stale transcript

    async def start_session(self, system_prompt: str = ''):
        """Spawn the persistent Node STT subprocess (called once per WS connection)."""
        if self.proc and self.proc.returncode is None:
            logger.info("Nova Sonic: session already running, skipping re-start")
            return

        self._system_prompt = system_prompt or ''
        logger.info("Nova Sonic: starting persistent session process")

        args = ["node", _STT_SCRIPT]
        if system_prompt:
            args.append(system_prompt)

        try:
            self.proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.reader_task = asyncio.create_task(self._read_stdout())
            self.stderr_task = asyncio.create_task(self._read_stderr())
            self.is_active = True
            logger.info(f"Nova Sonic: persistent session process started (pid={self.proc.pid})")
        except Exception as e:
            logger.error(f"Nova Sonic: failed to start STT process: {e}")
            self.is_active = False

    async def start_audio_input(self):
        """Send START_PROMPT to begin a new user turn."""
        if not self.is_active or not self.proc or self.proc.returncode is not None:
            # Process died — restart
            logger.info("Nova Sonic: process dead, restarting session")
            await self.start_session(self._system_prompt)

        # Wait until Bedrock has finished its previous prompt before starting a new one.
        # In practice TTS playback takes several seconds, during which Bedrock finishes
        # and emits promptEnd → BEDROCK_DONE — so this wait is usually a no-op.
        try:
            await asyncio.wait_for(self._bedrock_done.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("Nova Sonic: timeout waiting for BEDROCK_DONE — proceeding anyway")
        self._bedrock_done.clear()

        self._transcript_ready.clear()
        self._ready_event.clear()
        self._is_processing = True

        try:
            self.proc.stdin.write(b'START_PROMPT\n')
            await self.proc.stdin.drain()
            logger.info("Nova Sonic: START_PROMPT sent")
        except Exception as e:
            logger.error(f"Nova Sonic: error sending START_PROMPT: {e}")

    async def send_audio_chunk(self, base64_audio: str):
        if not self.is_active or not self.proc or not self.proc.stdin:
            return
        try:
            self.proc.stdin.write(f"{base64_audio}\n".encode())
            await self.proc.stdin.drain()
        except Exception as e:
            logger.error(f"Nova Sonic: error writing audio chunk: {e}")

    async def end_audio_input(self):
        """Send END_PROMPT and wait for TRANSCRIPT: line (up to 10s)."""
        if not self.is_active or not self.proc or not self.proc.stdin:
            return
        if not self._is_processing:
            # No prompt was started (e.g. client.audio.start was ignored while TTS played)
            return

        try:
            try:
                self.proc.stdin.write(b'END_PROMPT\n')
                await self.proc.stdin.drain()
                logger.info("Nova Sonic: END_PROMPT sent, waiting for transcript")
            except Exception as e:
                logger.error(f"Nova Sonic: error sending END_PROMPT: {e}")
                return

            try:
                await asyncio.wait_for(self._transcript_ready.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Nova Sonic: timeout waiting for TRANSCRIPT")
        finally:
            self._is_processing = False

    async def interrupt(self):
        """Interrupt an in-progress STT turn (called when TTS is cancelled mid-stream).

        Resets all per-turn Python state immediately so the next start_audio_input()
        can proceed without blocking.  If the Node is currently inside an audio turn
        (audioLoop), we send END_PROMPT so it properly closes the stream with Bedrock;
        the resulting stale transcript is suppressed via _ignore_next_transcript.
        """
        was_processing = self._is_processing
        self._is_processing = False
        # Unblock any coroutine waiting on these events so nothing hangs.
        self._transcript_ready.set()
        self._bedrock_done.set()

        if was_processing and self.proc and self.proc.stdin:
            # Node is inside audioLoop — tell it to close the current audio turn so
            # Bedrock doesn't get stuck waiting for more audio indefinitely.
            try:
                self.proc.stdin.write(b'END_PROMPT\n')
                await self.proc.stdin.drain()
                # Mark that the transcript Bedrock sends back for this abandoned turn
                # should be ignored (don't trigger a spurious graph run).
                self._ignore_next_transcript = True
            except Exception:
                pass

        logger.info(f"Nova Sonic: interrupted (was_processing={was_processing}), state reset")

    async def inject_assistant_text(self, text: str):
        """Inject the agent's response as ASSISTANT context into the Nova Sonic session."""
        if not self.is_active or not self.proc or not self.proc.stdin:
            return
        safe = text.replace('\n', ' ').replace('\r', ' ').strip()
        if not safe:
            return
        try:
            self.proc.stdin.write(f'INJECT_ASSISTANT:{safe}\n'.encode())
            await self.proc.stdin.drain()
            logger.info(f"Nova Sonic: injected assistant text ({len(safe)} chars)")
        except Exception as e:
            logger.warning(f"Nova Sonic: failed to inject assistant text: {e}")

    async def _read_stdout(self):
        """Persistent background task — reads output for the lifetime of the process."""
        try:
            while self.proc and self.proc.stdout:
                line = await self.proc.stdout.readline()
                if not line:
                    break

                decoded = line.decode().strip()
                if decoded:
                    logger.info(f"Nova Sonic STT (stdout): {decoded[:120]}")

                if decoded.startswith("TRANSCRIPT_PARTIAL:"):
                    partial = decoded[len("TRANSCRIPT_PARTIAL:"):].strip()
                    if partial and self.on_text_chunk:
                        res = self.on_text_chunk(partial, is_user=True, is_final=False)
                        if asyncio.iscoroutine(res): await res

                elif decoded.startswith("TRANSCRIPT:"):
                    final = decoded[len("TRANSCRIPT:"):].strip()
                    # Check whether this transcript is from an interrupted/abandoned turn.
                    should_ignore = self._ignore_next_transcript
                    self._ignore_next_transcript = False

                    if should_ignore:
                        # Stale transcript from the turn that was interrupted — drop it
                        # so we don't fire a spurious graph run.  Do NOT set
                        # _transcript_ready here; the next real turn's TRANSCRIPT will.
                        logger.info("Nova Sonic: ignoring stale transcript from interrupted turn")
                    else:
                        # Deliver transcript first (sets user_transcripts in session_data)
                        if self.on_text_chunk:
                            res = self.on_text_chunk(final, is_user=True, is_final=True)
                            if asyncio.iscoroutine(res): await res
                        # Unblock end_audio_input() waiter
                        self._transcript_ready.set()
                        # Trigger graph run as a background task (don't block the reader)
                        if self.on_finished:
                            asyncio.create_task(self.on_finished())

                elif decoded == 'READY':
                    self._ready_event.set()

                elif decoded == 'BEDROCK_DONE':
                    self._bedrock_done.set()
                    logger.info("Nova Sonic: BEDROCK_DONE — Bedrock prompt fully complete")

            logger.info("Nova Sonic: STT process stdout closed")
        except Exception as e:
            logger.error(f"Nova Sonic: error in stdout reader: {e}")
        finally:
            # Unblock any waiters so nothing hangs on process death
            self._transcript_ready.set()
            self._ready_event.set()
            self._bedrock_done.set()
            self.is_active = False

    async def _read_stderr(self):
        """Task to stream stderr to logs in real-time."""
        try:
            while self.proc and self.proc.stderr:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                logger.info(f"Nova Sonic STT (stderr): {line.decode().strip()}")
        except Exception as e:
            logger.error(f"Nova Sonic: error in stderr reader: {e}")

    async def end_session(self):
        """Gracefully shut down the STT session."""
        self.is_active = False
        self._is_processing = False

        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(b'SESSION_END\n')
                await self.proc.stdin.drain()
                self.proc.stdin.close()
            except Exception:
                pass

        if self.reader_task:
            try:
                await asyncio.wait_for(self.reader_task, timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass
            self.reader_task = None

        if self.proc:
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

        # Unblock any remaining waiters
        self._transcript_ready.set()
        self._ready_event.set()
        self._bedrock_done.set()
