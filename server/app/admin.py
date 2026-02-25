"""
admin.py — Agent Import System API.

Endpoints:
    POST /api/import-agent          Clone a GitHub repo and generate a plugin scaffold.
    GET  /api/import-agent/plugins  List currently registered plugins.

The import endpoint supports dry_run=true, which returns generated file
contents as strings without writing anything to disk.

Security notes:
  - plugin_id is validated against a strict regex before any filesystem access.
  - Source inspection uses ast-only (no exec/import of external code).
  - git clone is sandboxed to a temp directory with a 50 MB size guard and 30s timeout.
  - pip install runs automatically when requirements.txt is present in the imported repo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.agent.core.importer import langgraph_json, inspector, generator
from app.agent.core.importer.langgraph_json import LangGraphJsonError
from app.agent.core.importer.inspector import InspectionError
from app.agent.core.importer.generator import (
    GeneratorConfig,
    config_from_inspection,
    render,
    validate_plugin_id,
)
from app.agent.core.importer.llm_designer import design as llm_design, DesignResult
from app.agent.core.registry import list_plugins

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["admin"])

# ── Filesystem paths ──────────────────────────────────────────────────────────

_PLUGINS_ROOT = Path(__file__).parent / "agent" / "plugins"

# ── Constants ─────────────────────────────────────────────────────────────────

_CLONE_TIMEOUT_S   = 30
_MAX_REPO_SIZE_MB  = 50
_IMPORT_CHECK_TIMEOUT_S = 10
_PIP_INSTALL_TIMEOUT_S  = 120


# ── Request / Response models ─────────────────────────────────────────────────

class ImportRequest(BaseModel):
    url: str
    plugin_id: str
    strategy: str = "wrapper"   # "wrapper" | "subgraph" | "port"
    graph_id: Optional[str] = None
    dry_run: bool = False
    force: bool = False          # overwrite existing plugin directory
    use_llm: bool = True         # Phase 2: run LLM-assisted A2UI design
    screens_override: Optional[dict] = None  # skip LLM; use caller-supplied screens

    @field_validator("plugin_id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        try:
            validate_plugin_id(v)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return v

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        if v not in ("wrapper", "subgraph", "port"):
            raise ValueError("strategy must be one of: wrapper, subgraph, port")
        return v


class FilePreview(BaseModel):
    filename: str
    content: str


class SmokeTestResult(BaseModel):
    ok: bool
    outbox_count: int = 0
    has_a2ui: bool = False
    has_voice: bool = False
    outbox_sample: List[dict] = []   # first 3 outbox events (truncated)
    error: Optional[str] = None
    skipped: bool = False            # True when import check failed (can't smoke test)


class ValidationResult(BaseModel):
    import_ok: bool
    import_error: Optional[str] = None
    smoke_test: Optional[SmokeTestResult] = None


class ImportResponse(BaseModel):
    status: str                          # "ok" | "dry_run" | "error"
    plugin_id: str
    strategy: str
    graphs_found: List[str]
    graph_selected: str
    external_module: str
    state_class: Optional[str]
    state_fields: List[dict]
    detected_input_field: str
    detected_output_field: str
    dependencies: List[str]
    warnings: List[str]
    files: List[FilePreview]             # generated file contents
    files_written: List[str]             # paths actually written (empty if dry_run)
    requirements_to_install: List[str]   # lines from the cloned repo's requirements.txt
    validation: Optional[ValidationResult] = None
    # Phase 2 — LLM design
    llm_design_used: bool = False
    llm_used_fallback: bool = False
    llm_reasoning: Optional[str] = None
    llm_screens: Optional[dict] = None  # the A2UI screen definitions Claude produced


# ── Helpers ───────────────────────────────────────────────────────────────────

def _derive_module_path(file_path: str) -> str:
    """Convert a relative file path to a Python module path. e.g. my_agent/agent.py → my_agent.agent"""
    return file_path.replace("/", ".").removesuffix(".py")


def _read_readme(repo_root: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = repo_root / name
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")[:600]
    return ""


def _is_local_path(url: str) -> bool:
    """True when url is a filesystem path rather than a remote URL."""
    return not url.startswith(("http://", "https://", "git://", "ssh://", "git@"))


async def _git_clone(url: str, dest: Path) -> None:
    """Clone url into dest with depth=1. Raises HTTPException on failure."""
    cmd = ["git", "clone", "--depth", "1", "--quiet", url, str(dest)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CLONE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        raise HTTPException(502, f"git clone timed out after {_CLONE_TIMEOUT_S}s")
    except Exception as exc:
        raise HTTPException(502, f"git clone failed: {exc}")

    if proc.returncode != 0:
        msg = stderr.decode(errors="replace").strip()
        raise HTTPException(502, f"git clone failed: {msg}")

    # Size guard
    total_bytes = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    total_mb = total_bytes / (1024 * 1024)
    if total_mb > _MAX_REPO_SIZE_MB:
        shutil.rmtree(dest, ignore_errors=True)
        raise HTTPException(
            413,
            f"Repo is {total_mb:.1f} MB — exceeds the {_MAX_REPO_SIZE_MB} MB limit.",
        )


async def _acquire_repo(url: str, dest: Path) -> None:
    """Clone from a remote URL, or copy from a local filesystem path."""
    if _is_local_path(url):
        local = Path(url.replace("file://", "")).expanduser().resolve()
        if not local.exists():
            raise HTTPException(422, f"Local path not found: {local}")
        total_bytes = sum(f.stat().st_size for f in local.rglob("*") if f.is_file())
        total_mb = total_bytes / (1024 * 1024)
        if total_mb > _MAX_REPO_SIZE_MB:
            raise HTTPException(
                413,
                f"Directory is {total_mb:.1f} MB — exceeds the {_MAX_REPO_SIZE_MB} MB limit.",
            )
        shutil.copytree(local, dest, dirs_exist_ok=False)
        logger.info("[Import] Copied local path %s → %s", local, dest)
    else:
        await _git_clone(url, dest)


def _copy_source(repo_root: Path, plugin_dir: Path) -> None:
    """Copy the entire repo into plugin_dir/src/ so the external code is importable."""
    src_dir = plugin_dir / "src"
    if src_dir.exists():
        shutil.rmtree(src_dir)
    shutil.copytree(repo_root, src_dir, dirs_exist_ok=False)


def _write_files(plugin_dir: Path, files: Dict[str, str]) -> List[str]:
    """Write rendered file contents to plugin_dir. Returns list of written paths."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, content in files.items():
        dest = plugin_dir / filename
        dest.write_text(content, encoding="utf-8")
        written.append(str(dest))
        logger.info("[Import] Wrote %s", dest)
    return written


def _collect_requirements(repo_root: Path) -> List[str]:
    """
    Find requirements.txt files in the repo (root + one level deep) and
    return the merged, deduplicated list of non-comment package specifiers.
    """
    candidates = list(repo_root.glob("requirements.txt"))
    candidates += [
        p for p in repo_root.glob("*/requirements.txt")
        if p.parent != repo_root  # already covered by glob above
    ]

    seen: dict[str, None] = {}  # ordered dedup
    for req_file in candidates:
        for raw in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                seen[line] = None

    return list(seen)


def _write_requirements(plugin_dir: Path, requirements: List[str]) -> Optional[str]:
    """
    Write requirements_import.txt to the plugin directory.
    Returns the written path, or None if requirements is empty.
    """
    if not requirements:
        return None
    dest = plugin_dir / "requirements_import.txt"
    dest.write_text(
        "# Dependencies required by this imported agent.\n"
        "# Install with: pip install -r requirements_import.txt\n"
        + "\n".join(requirements) + "\n",
        encoding="utf-8",
    )
    logger.info("[Import] Wrote %s", dest)
    return str(dest)


_SERVER_DIR = Path(__file__).parent.parent   # server/
_SMOKE_TEST_TIMEOUT_S = 15


async def _install_requirements(requirements: List[str], plugin_dir: Path) -> tuple[bool, str]:
    """
    Install agent dependencies into the running venv with pip.

    Uses sys.executable so the same venv that runs the server receives the
    packages — identical to the fix applied to the import check subprocess.
    Returns (ok, error_message).
    """
    if not requirements:
        return True, ""

    req_file = plugin_dir / "requirements_import.txt"
    cmd = (
        [sys.executable, "-m", "pip", "install", "-r", str(req_file)]
        if req_file.exists()
        else [sys.executable, "-m", "pip", "install"] + requirements
    )
    logger.info("[Import] Installing %d requirement(s): %s", len(requirements), requirements)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_PIP_INSTALL_TIMEOUT_S
        )
        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode(errors="replace")[:800]
            logger.error("[Import] pip install failed (rc=%d): %s", proc.returncode, err)
            return False, err
        logger.info("[Import] pip install succeeded")
        return True, ""
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"pip install timed out after {_PIP_INSTALL_TIMEOUT_S} s"
    except Exception as exc:
        return False, str(exc)


def _run_import_check(plugin_id: str, plugin_class_name: str) -> ValidationResult:
    """
    Subprocess import check: verify the generated plugin can be loaded and
    instantiated without errors.  Returns a ValidationResult (no smoke_test yet).
    """
    check_code = (
        f"import sys; sys.path.insert(0, '.');"           # server/ is cwd
        f"from app.agent.plugins.{plugin_id}.plugin import {plugin_class_name}; "
        f"p = {plugin_class_name}(); "
        f"assert p.plugin_id == '{plugin_id}'; "
        f"print('OK')"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", check_code],
            cwd=str(_SERVER_DIR),
            capture_output=True,
            text=True,
            timeout=_IMPORT_CHECK_TIMEOUT_S,
        )
        ok = proc.returncode == 0 and "OK" in proc.stdout
        error = None if ok else (proc.stderr.strip() or proc.stdout.strip())[:1000]
        return ValidationResult(import_ok=ok, import_error=error)
    except subprocess.TimeoutExpired:
        return ValidationResult(import_ok=False, import_error="Import check timed out")
    except Exception as exc:
        return ValidationResult(import_ok=False, import_error=str(exc))


def _run_smoke_test(plugin_id: str, plugin_class_name: str) -> SmokeTestResult:
    """
    Invoke the generated plugin's graph with an empty transcript (welcome screen path).

    Uses an empty transcript deliberately: the thin-wrapper routes this to
    handle_welcome, which exercises the plugin/graph/A2UI plumbing without
    calling the external agent (which may have unresolved dependencies).

    Returns a SmokeTestResult describing what the outbox contained.
    """
    smoke_code = f"""
import json, sys
sys.path.insert(0, '.')
from app.agent.plugins.{plugin_id}.plugin import {plugin_class_name}

plugin = {plugin_class_name}()
state = plugin.create_initial_state()
state['transcript'] = ''        # empty → welcome screen, no external graph call
state['pendingAction'] = None

graph = plugin.build_graph()
result = graph.invoke(state, {{}})
outbox = result.get('outbox', [])

# Truncate large component payloads for safe JSON serialisation
def _safe_event(e):
    e = dict(e)
    if 'payload' in e and isinstance(e['payload'], dict):
        p = dict(e['payload'])
        if 'updateComponents' in p and isinstance(p.get('updateComponents'), dict):
            comps = p['updateComponents'].get('components', [])
            p['updateComponents'] = {{'component_count': len(comps), 'first': comps[:2]}}
        if 'text' in p:
            p['text'] = p['text'][:120]
        e['payload'] = p
    return e

sample = [_safe_event(e) for e in outbox[:3]]

print(json.dumps({{
    'ok': True,
    'outbox_count': len(outbox),
    'has_a2ui': any(e.get('type') == 'server.a2ui.patch' for e in outbox),
    'has_voice': any(e.get('type') == 'server.voice.say' for e in outbox),
    'outbox_sample': sample,
}}))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", smoke_code],
            cwd=str(_SERVER_DIR),
            capture_output=True,
            text=True,
            timeout=_SMOKE_TEST_TIMEOUT_S,
        )
        if proc.returncode != 0:
            error = (proc.stderr.strip() or proc.stdout.strip())[:1000]
            logger.warning("[Import] Smoke test failed (rc=%d): %s", proc.returncode, error)
            return SmokeTestResult(ok=False, error=error)

        # Parse the JSON line emitted by the smoke code
        output = proc.stdout.strip()
        data = json.loads(output)
        return SmokeTestResult(
            ok=data.get("ok", False),
            outbox_count=data.get("outbox_count", 0),
            has_a2ui=data.get("has_a2ui", False),
            has_voice=data.get("has_voice", False),
            outbox_sample=data.get("outbox_sample", []),
        )
    except subprocess.TimeoutExpired:
        return SmokeTestResult(ok=False, error=f"Smoke test timed out after {_SMOKE_TEST_TIMEOUT_S}s")
    except json.JSONDecodeError as exc:
        return SmokeTestResult(ok=False, error=f"Smoke test output was not valid JSON: {exc}")
    except Exception as exc:
        return SmokeTestResult(ok=False, error=str(exc))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/import-agent", response_model=ImportResponse)
async def import_agent(req: ImportRequest) -> ImportResponse:
    """
    Clone a LangGraph agent repo and generate a plugin scaffold.

    Set dry_run=true to preview generated files without writing to disk.
    """
    logger.info("[Import] Request: plugin_id=%s url=%s dry_run=%s",
                req.plugin_id, req.url, req.dry_run)

    plugin_dir = _PLUGINS_ROOT / req.plugin_id

    # Guard: refuse to overwrite without force=true
    if plugin_dir.exists() and not req.force and not req.dry_run:
        raise HTTPException(
            409,
            f"Plugin '{req.plugin_id}' already exists. "
            "Pass force=true to overwrite.",
        )

    warnings: List[str] = []
    tmpdir = tempfile.mkdtemp(prefix="ais_clone_")
    repo_root = Path(tmpdir) / "repo"

    try:
        # ── Step 1: Fetch (clone or copy) ─────────────────────────────────────
        logger.info("[Import] Acquiring %s", req.url)
        await _acquire_repo(req.url, repo_root)

        # ── Step 2: Parse langgraph.json ──────────────────────────────────────
        try:
            lg_config = langgraph_json.parse(repo_root)
        except LangGraphJsonError as exc:
            raise HTTPException(422, str(exc))

        graph_entry = langgraph_json.pick_graph(lg_config, req.graph_id)
        logger.info("[Import] Selected graph: %s → %s:%s",
                    graph_entry.graph_id, graph_entry.file_path, graph_entry.export_name)

        # ── Step 3: AST inspection ────────────────────────────────────────────
        try:
            result = inspector.inspect_graph_entry(repo_root, graph_entry.file_path)
        except InspectionError as exc:
            raise HTTPException(422, str(exc))

        warnings.extend(result.warnings)
        readme_excerpt = _read_readme(repo_root)
        requirements = _collect_requirements(repo_root)

        # ── Step 4a: A2UI design ──────────────────────────────────────────────
        external_module = _derive_module_path(graph_entry.file_path)
        llm_result: Optional[DesignResult] = None

        if req.screens_override is not None:
            # Caller supplied edited screens — skip LLM entirely
            logger.info("[Import] Using caller-supplied screens_override for %s", req.plugin_id)
            llm_result = DesignResult(
                screens=req.screens_override,
                input_field=result.detected_input_field,
                output_accessor=result.detected_output_field,
                initial_domain_state={},
                reasoning="User-edited screens (screens_override supplied).",
                used_fallback=False,
            )
        elif req.use_llm:
            logger.info("[Import] Running LLM A2UI design (plugin_id=%s)", req.plugin_id)
            try:
                llm_result = await llm_design(
                    plugin_id=req.plugin_id,
                    inspection=result,
                    readme_excerpt=readme_excerpt,
                )
                if llm_result.used_fallback:
                    warnings.append(
                        f"LLM design fell back to defaults: {llm_result.fallback_reason}"
                    )
                logger.info(
                    "[Import] LLM design done. fallback=%s screens=%s",
                    llm_result.used_fallback,
                    list(llm_result.screens.keys()),
                )
            except Exception as exc:
                warnings.append(f"LLM design raised an unexpected error: {exc}")
                logger.error("[Import] LLM design error: %s", exc, exc_info=True)

        # ── Step 4b: Build generator config ──────────────────────────────────
        gen_config: GeneratorConfig = config_from_inspection(
            inspection=result,
            plugin_id=req.plugin_id,
            external_module=external_module,
            graph_export=graph_entry.export_name,
            readme_excerpt=readme_excerpt,
            # Inject LLM-designed screens + field mappings when available
            screens=llm_result.screens if llm_result else None,
            input_field_override=llm_result.input_field if llm_result else None,
            output_accessor_override=llm_result.output_accessor if llm_result else None,
            initial_domain_state_override=(
                llm_result.initial_domain_state if llm_result else None
            ),
        )

        # ── Step 5: Render templates ──────────────────────────────────────────
        rendered: Dict[str, str] = render(gen_config)
        file_previews = [
            FilePreview(filename=k, content=v) for k, v in rendered.items()
        ]

        # ── Step 6: Write (unless dry_run) ───────────────────────────────────
        files_written: List[str] = []
        validation: Optional[ValidationResult] = None

        if not req.dry_run:
            _write_files(plugin_dir, rendered)
            files_written = [str(plugin_dir / fn) for fn in rendered]
            _copy_source(repo_root, plugin_dir)

            # Write requirements_import.txt alongside the plugin files
            req_path = _write_requirements(plugin_dir, requirements)
            if req_path:
                files_written.append(req_path)

            # ── Step 6b: Install dependencies ────────────────────────────────
            if requirements:
                pip_ok, pip_err = await _install_requirements(requirements, plugin_dir)
                if not pip_ok:
                    warnings.append(
                        f"Dependency install failed: {pip_err}. "
                        "The smoke test may fail. Install manually with: "
                        f"pip install -r {plugin_dir}/requirements_import.txt"
                    )

            # ── Step 7: Import check ──────────────────────────────────────────
            validation = _run_import_check(req.plugin_id, gen_config.plugin_class_name)
            if not validation.import_ok:
                warnings.append(
                    f"Import check failed: {validation.import_error}. "
                    "The plugin was written to disk but may need manual fixes."
                )
                # Skip smoke test — graph won't be invokable
                validation.smoke_test = SmokeTestResult(ok=False, skipped=True,
                    error="Skipped: import check failed")
            else:
                # ── Step 8: Smoke test ────────────────────────────────────────
                logger.info("[Import] Running smoke test for %s", req.plugin_id)
                smoke = _run_smoke_test(req.plugin_id, gen_config.plugin_class_name)
                validation.smoke_test = smoke
                if not smoke.ok:
                    warnings.append(
                        f"Smoke test failed: {smoke.error}. "
                        "The plugin imported correctly but the graph raised an error."
                    )
                logger.info(
                    "[Import] Smoke test: ok=%s has_a2ui=%s has_voice=%s outbox_count=%d",
                    smoke.ok, smoke.has_a2ui, smoke.has_voice, smoke.outbox_count,
                )

            logger.info("[Import] Done. import_ok=%s smoke_ok=%s",
                        validation.import_ok,
                        validation.smoke_test.ok if validation.smoke_test else "n/a")
        else:
            logger.info("[Import] Dry run complete.")

        return ImportResponse(
            status="dry_run" if req.dry_run else "ok",
            plugin_id=req.plugin_id,
            strategy=req.strategy,
            graphs_found=[g.graph_id for g in lg_config.graphs],
            graph_selected=graph_entry.graph_id,
            external_module=external_module,
            state_class=result.state_class,
            state_fields=[
                {"name": f.name, "annotation": f.annotation}
                for f in result.state_fields
            ],
            detected_input_field=result.detected_input_field,
            detected_output_field=result.detected_output_field,
            dependencies=lg_config.dependencies,
            warnings=warnings,
            requirements_to_install=requirements,
            files=file_previews,
            files_written=files_written,
            validation=validation,
            llm_design_used=llm_result is not None,
            llm_used_fallback=llm_result.used_fallback if llm_result else False,
            llm_reasoning=llm_result.reasoning if llm_result else None,
            llm_screens=llm_result.screens if llm_result else None,
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        logger.debug("[Import] Cleaned up temp dir %s", tmpdir)


@router.get("/import-agent/plugins")
async def list_registered_plugins() -> Dict[str, Any]:
    """Return all currently registered plugin IDs."""
    return {"plugins": list_plugins()}
