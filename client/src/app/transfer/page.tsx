'use client';

import { useState, useCallback } from 'react';
import Link from 'next/link';
import A2Renderer from '@/components/A2Renderer';
import type { A2UIPayload, A2UIComponent } from '@/components/A2Renderer';

// ── Types ─────────────────────────────────────────────────────────────────────

type WizardStep =
  | 'idle'
  | 'analysing'
  | 'preview'
  | 'deps'
  | 'installing'
  | 'done'
  | 'error';

interface ScreenDef {
  title: string;
  voice_text: string;
  components: A2UIComponent[];
}

interface FilePreview {
  filename: string;
  content: string;
}

interface SmokeTestResult {
  ok: boolean;
  outbox_count: number;
  has_a2ui: boolean;
  has_voice: boolean;
  outbox_sample: Record<string, unknown>[];
  error: string | null;
  skipped: boolean;
}

interface ApiResult {
  status: string;
  plugin_id: string;
  strategy: string;
  graphs_found: string[];
  graph_selected: string;
  external_module: string;
  state_class: string | null;
  state_fields: Array<{ name: string; annotation: string }>;
  detected_input_field: string;
  detected_output_field: string;
  dependencies: string[];
  requirements_to_install: string[];
  warnings: string[];
  files: FilePreview[];
  files_written: string[];
  llm_design_used: boolean;
  llm_used_fallback: boolean;
  llm_reasoning: string | null;
  llm_screens: Record<string, ScreenDef> | null;
  validation: {
    import_ok: boolean;
    import_error: string | null;
    smoke_test: SmokeTestResult | null;
  } | null;
}

// ── API helpers ───────────────────────────────────────────────────────────────

function getApiBase(): string {
  const wsUrl =
    process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws';
  return wsUrl.replace(/^ws/, 'http').split('/ws')[0];
}

async function callImportApi(
  body: Record<string, unknown>
): Promise<ApiResult> {
  // Use the Next.js proxy rewrite (/api/* → FastAPI) — no hardcoded host needed.
  const res = await fetch('/api/import-agent', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res
      .json()
      .catch(() => ({ detail: res.statusText }));
    throw new Error(
      (err as { detail?: string }).detail || `HTTP ${res.status}`
    );
  }
  return res.json() as Promise<ApiResult>;
}

// ── Conversion helpers ────────────────────────────────────────────────────────

function screenToPayload(screen: ScreenDef): A2UIPayload {
  return {
    version: '1.0',
    updateComponents: {
      surfaceId: 'main',
      components: screen.components,
    },
  };
}

// ── Step indicator ────────────────────────────────────────────────────────────

const STEPS: { key: WizardStep; label: string }[] = [
  { key: 'idle', label: 'Configure' },
  { key: 'analysing', label: 'Analyse' },
  { key: 'preview', label: 'Preview' },
  { key: 'deps', label: 'Dependencies' },
  { key: 'installing', label: 'Install' },
  { key: 'done', label: 'Done' },
];

function StepIndicator({ current }: { current: WizardStep }) {
  const activeIdx = STEPS.findIndex((s) => s.key === current);
  return (
    <div className="flex items-center gap-0 mb-8 flex-wrap gap-y-2">
      {STEPS.map((step, i) => (
        <div key={step.key} className="flex items-center">
          <div
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all ${
              i === activeIdx
                ? 'bg-blue-600 text-white shadow-sm'
                : i < activeIdx
                ? 'bg-blue-100 text-blue-700'
                : 'bg-gray-100 text-gray-400'
            }`}
          >
            <span
              className={`w-4 h-4 rounded-full text-[9px] flex items-center justify-center font-bold ${
                i < activeIdx
                  ? 'bg-blue-600 text-white'
                  : i === activeIdx
                  ? 'bg-white/30 text-white'
                  : 'bg-gray-300 text-gray-500'
              }`}
            >
              {i < activeIdx ? '✓' : i + 1}
            </span>
            {step.label}
          </div>
          {i < STEPS.length - 1 && (
            <div
              className={`h-px w-5 ${
                i < activeIdx ? 'bg-blue-300' : 'bg-gray-200'
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ── Spinner ───────────────────────────────────────────────────────────────────

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-5">
      <div className="w-12 h-12 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin" />
      <p className="text-blue-700 font-medium text-sm">{label}</p>
    </div>
  );
}

// ── Warning badge ─────────────────────────────────────────────────────────────

function Warnings({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
      <p className="text-xs font-semibold text-amber-700 mb-2 uppercase tracking-wide">
        Warnings
      </p>
      <ul className="space-y-1">
        {warnings.map((w, i) => (
          <li key={i} className="text-xs text-amber-800 flex gap-2">
            <span>⚠</span>
            <span>{w}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Status pill ───────────────────────────────────────────────────────────────

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
        ok
          ? 'bg-green-100 text-green-700'
          : 'bg-red-100 text-red-700'
      }`}
    >
      <span>{ok ? '✓' : '✗'}</span>
      {label}
    </span>
  );
}

// ── Code block ────────────────────────────────────────────────────────────────

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [code]);

  return (
    <div className="relative h-full">
      <button
        onClick={copy}
        className="absolute top-3 right-3 z-10 px-2 py-1 rounded text-[10px] font-medium bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors"
      >
        {copied ? 'Copied!' : 'Copy'}
      </button>
      <pre className="h-full overflow-auto bg-slate-900 text-slate-200 text-xs leading-relaxed p-4 rounded-xl font-mono whitespace-pre-wrap">
        {code}
      </pre>
    </div>
  );
}

// ── Main wizard page ──────────────────────────────────────────────────────────

export default function TransferPage() {
  const [step, setStep] = useState<WizardStep>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  // Form state
  const [url, setUrl] = useState('');
  const [pluginId, setPluginId] = useState('');
  const [strategy, setStrategy] = useState<'wrapper' | 'subgraph' | 'port'>('wrapper');
  const [useLlm, setUseLlm] = useState(true);

  // Results
  const [preview, setPreview] = useState<ApiResult | null>(null);
  const [writeResult, setWriteResult] = useState<ApiResult | null>(null);

  // Preview navigation
  const [activeFile, setActiveFile] = useState(0);
  const [activeScreen, setActiveScreen] = useState('welcome');

  // Inline JSON editor
  const [editMode, setEditMode] = useState(false);
  const [editedScreens, setEditedScreens] = useState<Record<string, ScreenDef> | null>(null);
  const [jsonEditText, setJsonEditText] = useState('');
  const [jsonError, setJsonError] = useState('');

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleAnalyse = useCallback(async () => {
    if (!url.trim() || !pluginId.trim()) return;
    setStep('analysing');
    setErrorMsg('');
    setEditMode(false);
    setEditedScreens(null);
    setJsonEditText('');
    setJsonError('');
    try {
      const result = await callImportApi({
        url: url.trim(),
        plugin_id: pluginId.trim(),
        strategy,
        use_llm: useLlm,
        dry_run: true,
      });
      setPreview(result);
      // Default to first available screen
      if (result.llm_screens) {
        setActiveScreen(Object.keys(result.llm_screens)[0] || 'welcome');
      }
      setActiveFile(0);
      setStep('preview');
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setStep('error');
    }
  }, [url, pluginId, strategy, useLlm]);

  const handleInstall = useCallback(async () => {
    if (!preview) return;
    setStep('installing');
    try {
      const body: Record<string, unknown> = {
        url: url.trim(),
        plugin_id: pluginId.trim(),
        strategy,
        dry_run: false,
        force: true,
      };
      // If the user edited any screens, send them as an override (skips LLM re-run)
      if (editedScreens) {
        body.screens_override = editedScreens;
        body.use_llm = false;
      } else {
        body.use_llm = useLlm;
      }
      const result = await callImportApi(body);
      setWriteResult(result);
      setStep('done');
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setStep('error');
    }
  }, [preview, url, pluginId, strategy, useLlm, editedScreens]);

  const reset = useCallback(() => {
    setStep('idle');
    setPreview(null);
    setWriteResult(null);
    setErrorMsg('');
    setEditMode(false);
    setEditedScreens(null);
    setJsonEditText('');
    setJsonError('');
  }, []);

  // Switch screen: load its (possibly edited) JSON into the editor textarea
  const handleSelectScreen = useCallback((key: string, screens: Record<string, ScreenDef>) => {
    setActiveScreen(key);
    if (editMode) {
      const current = editedScreens?.[key] ?? screens[key];
      setJsonEditText(JSON.stringify(current, null, 2));
      setJsonError('');
    }
  }, [editMode, editedScreens]);

  // Handle JSON edits: parse and update editedScreens live
  const handleJsonEdit = useCallback((text: string, screens: Record<string, ScreenDef>) => {
    setJsonEditText(text);
    try {
      const parsed = JSON.parse(text) as ScreenDef;
      setEditedScreens((prev) => ({ ...screens, ...(prev ?? {}), [activeScreen]: parsed }));
      setJsonError('');
    } catch {
      setJsonError('Invalid JSON');
    }
  }, [activeScreen]);

  // ── Rendered panels ────────────────────────────────────────────────────────

  const renderIdle = () => (
    <div className="max-w-xl mx-auto">
      <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8">
        <h2 className="text-lg font-bold text-blue-950 mb-1">
          Import a LangGraph Agent
        </h2>
        <p className="text-sm text-gray-500 mb-6">
          Paste a GitHub URL or an absolute local path to a directory that
          contains a{' '}
          <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">
            langgraph.json
          </code>{' '}
          config. The agent will be wrapped in a plugin scaffold and{' '}
          {useLlm ? 'its A2UI screens designed by Claude Sonnet' : 'a minimal A2UI scaffold generated'}.
        </p>

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1.5 uppercase tracking-wide">
              GitHub URL or local path
            </label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://github.com/org/repo  or  /absolute/path/to/agent"
              className="w-full px-4 py-2.5 rounded-xl border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1.5 uppercase tracking-wide">
              Plugin ID{' '}
              <span className="text-gray-400 font-normal normal-case">
                (lowercase, underscores only)
              </span>
            </label>
            <input
              type="text"
              value={pluginId}
              onChange={(e) =>
                setPluginId(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'))
              }
              placeholder="my_agent"
              className="w-full px-4 py-2.5 rounded-xl border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono"
            />
          </div>

          {/* Strategy selector */}
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-2 uppercase tracking-wide">
              Integration strategy
            </label>
            <div className="grid grid-cols-3 gap-2">
              {(
                [
                  { key: 'wrapper', label: 'Thin Wrapper', desc: 'Runs the graph as-is inside a single node. Best for text-in / text-out agents.' },
                  { key: 'subgraph', label: 'Sub-graph', desc: 'Embeds the graph as a subgraph. Best for structured-output agents.' },
                  { key: 'port', label: 'Full Port', desc: 'Generates shells only — manual wiring required. Best for custom flows.' },
                ] as { key: 'wrapper' | 'subgraph' | 'port'; label: string; desc: string }[]
              ).map(({ key, label, desc }) => (
                <button
                  key={key}
                  onClick={() => setStrategy(key)}
                  className={`rounded-xl border p-3 text-left transition-all ${
                    strategy === key
                      ? 'border-blue-500 bg-blue-50 ring-1 ring-blue-500'
                      : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <p className={`text-xs font-semibold mb-0.5 ${strategy === key ? 'text-blue-700' : 'text-gray-700'}`}>
                    {label}
                  </p>
                  <p className="text-[10px] text-gray-500 leading-snug">{desc}</p>
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={() => setUseLlm((v) => !v)}
              className={`relative w-10 h-6 rounded-full transition-colors ${
                useLlm ? 'bg-blue-600' : 'bg-gray-300'
              }`}
            >
              <span
                className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-all ${
                  useLlm ? 'left-5' : 'left-1'
                }`}
              />
            </button>
            <span className="text-sm text-gray-700">
              Use Claude Sonnet to design A2UI screens
            </span>
          </div>
        </div>

        <button
          onClick={handleAnalyse}
          disabled={!url.trim() || !pluginId.trim()}
          className="mt-8 w-full py-3 rounded-xl bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 active:scale-[0.98] transition-all disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Analyse & Preview →
        </button>
      </div>

      <p className="text-center text-xs text-gray-400 mt-4">
        No code is executed during analysis — source inspection is read-only (AST only).
      </p>
    </div>
  );

  const renderPreview = () => {
    if (!preview) return null;
    const screens = preview.llm_screens || {};
    const screenKeys = Object.keys(screens);
    // Merge edits on top of originals so the preview reflects changes
    const effectiveScreens = editedScreens
      ? { ...screens, ...editedScreens }
      : screens;
    const currentScreen = effectiveScreens[activeScreen];
    const a2uiPayload = currentScreen ? screenToPayload(currentScreen) : null;
    const hasEdits = editedScreens !== null && Object.keys(editedScreens).length > 0;

    return (
      <div className="space-y-4">
        {/* Schema summary bar */}
        <div className="bg-white rounded-xl border border-gray-100 px-5 py-3 flex flex-wrap gap-4 text-xs text-gray-600 items-center">
          <span>
            <span className="font-semibold text-blue-950">Graph:</span>{' '}
            {preview.graph_selected}
          </span>
          <span>
            <span className="font-semibold text-blue-950">Strategy:</span>{' '}
            {preview.strategy}
          </span>
          <span>
            <span className="font-semibold text-blue-950">State:</span>{' '}
            {preview.state_class || 'unknown'}
            {preview.state_fields.length > 0 &&
              ` (${preview.state_fields.map((f) => f.name).join(', ')})`}
          </span>
          <span>
            <span className="font-semibold text-blue-950">Input:</span>{' '}
            <code className="bg-gray-100 px-1 rounded">
              {preview.detected_input_field}
            </code>
          </span>
          <span>
            <span className="font-semibold text-blue-950">Output:</span>{' '}
            <code className="bg-gray-100 px-1 rounded">
              {preview.detected_output_field}
            </code>
          </span>
          {preview.llm_design_used && (
            <span className="ml-auto flex items-center gap-1 text-blue-600 font-semibold">
              <span>✦</span>
              {preview.llm_used_fallback ? 'LLM fallback' : 'Claude Sonnet designed'}
            </span>
          )}
        </div>

        {/* Two-panel layout */}
        <div className="grid grid-cols-2 gap-4 h-[540px]">
          {/* Left: generated files */}
          <div className="bg-white rounded-2xl border border-gray-100 flex flex-col overflow-hidden">
            <div className="flex border-b border-gray-100">
              {preview.files.map((f, i) => (
                <button
                  key={f.filename}
                  onClick={() => setActiveFile(i)}
                  className={`px-4 py-2.5 text-xs font-mono font-medium border-r border-gray-100 transition-colors ${
                    i === activeFile
                      ? 'bg-slate-900 text-slate-200'
                      : 'text-gray-500 hover:bg-gray-50'
                  }`}
                >
                  {f.filename}
                </button>
              ))}
            </div>
            <div className="flex-1 overflow-hidden p-2">
              {preview.files[activeFile] && (
                <CodeBlock code={preview.files[activeFile].content} />
              )}
            </div>
          </div>

          {/* Right: A2UI screen preview + inline editor */}
          <div className="bg-white rounded-2xl border border-gray-100 flex flex-col overflow-hidden">
            {/* Header: screen tabs + edit toggle */}
            <div className="flex items-center gap-2 border-b border-gray-100 px-3 py-2 flex-wrap">
              <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                A2UI
              </span>
              <div className="flex gap-1 flex-wrap flex-1">
                {screenKeys.map((key) => (
                  <button
                    key={key}
                    onClick={() => handleSelectScreen(key, screens)}
                    className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                      key === activeScreen
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                    }`}
                  >
                    {key}
                    {editedScreens?.[key] && (
                      <span className="ml-1 text-amber-400">•</span>
                    )}
                  </button>
                ))}
                {screenKeys.length === 0 && (
                  <span className="text-xs text-gray-400">No screens</span>
                )}
              </div>
              {screenKeys.length > 0 && (
                <button
                  onClick={() => {
                    const next = !editMode;
                    setEditMode(next);
                    if (next) {
                      const current = effectiveScreens[activeScreen];
                      setJsonEditText(current ? JSON.stringify(current, null, 2) : '{}');
                      setJsonError('');
                    }
                  }}
                  className={`shrink-0 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                    editMode
                      ? 'bg-amber-100 text-amber-700 ring-1 ring-amber-300'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {editMode ? 'Preview' : '✎ Edit JSON'}
                </button>
              )}
            </div>

            {/* Body: preview or editor */}
            {editMode ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                <textarea
                  value={jsonEditText}
                  onChange={(e) => handleJsonEdit(e.target.value, screens)}
                  spellCheck={false}
                  className={`flex-1 resize-none font-mono text-xs p-3 focus:outline-none bg-slate-900 text-slate-200 leading-relaxed ${
                    jsonError ? 'border-b-2 border-red-500' : ''
                  }`}
                />
                {jsonError && (
                  <div className="px-3 py-1.5 bg-red-50 border-t border-red-200">
                    <p className="text-xs text-red-600 font-mono">{jsonError}</p>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex-1 overflow-auto p-4">
                {a2uiPayload ? (
                  <A2Renderer
                    a2uiState={a2uiPayload}
                    isMobile={false}
                    onAction={(id: string, data?: Record<string, unknown>) =>
                      console.log('[Preview] Action:', id, data)
                    }
                  />
                ) : (
                  <div className="flex items-center justify-center h-full text-sm text-gray-400">
                    No screen to preview
                  </div>
                )}
              </div>
            )}

            {/* Voice text bar */}
            {currentScreen?.voice_text && !editMode && (
              <div className="border-t border-gray-100 px-4 py-2.5 bg-blue-50">
                <p className="text-xs text-blue-700">
                  <span className="font-semibold">Voice:</span>{' '}
                  {currentScreen.voice_text.replace('{response}', '[agent response]')}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Edit notice */}
        {hasEdits && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 flex items-center gap-2">
            <span className="text-amber-600 text-sm">✎</span>
            <p className="text-xs text-amber-800">
              You&apos;ve edited {Object.keys(editedScreens!).length} screen(s). These changes will be
              used when the plugin is written — the LLM won&apos;t re-run.
            </p>
            <button
              onClick={() => { setEditedScreens(null); setJsonEditText(''); setJsonError(''); }}
              className="ml-auto text-xs text-amber-600 underline hover:text-amber-800"
            >
              Discard edits
            </button>
          </div>
        )}

        {/* LLM reasoning */}
        {preview.llm_reasoning && (
          <div className="bg-white rounded-xl border border-blue-100 px-5 py-4">
            <p className="text-xs font-semibold text-blue-700 uppercase tracking-wide mb-1">
              ✦ Claude&apos;s design reasoning
            </p>
            <p className="text-sm text-gray-700">{preview.llm_reasoning}</p>
          </div>
        )}

        <Warnings warnings={preview.warnings} />

        {/* Action bar */}
        <div className="flex items-center gap-3 pt-2">
          <button
            onClick={reset}
            className="px-5 py-2.5 rounded-xl border border-gray-200 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
          >
            ← Start over
          </button>
          <button
            onClick={() => setStep('deps')}
            className="flex-1 py-2.5 rounded-xl bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 active:scale-[0.98] transition-all"
          >
            Looks good — Check dependencies →
          </button>
        </div>
      </div>
    );
  };

  const renderDeps = () => {
    if (!preview) return null;
    const hasDeps = preview.requirements_to_install.length > 0;

    return (
      <div className="max-w-xl mx-auto space-y-4">
        <div className="bg-white rounded-2xl border border-gray-100 p-8">
          <h2 className="text-lg font-bold text-blue-950 mb-1">
            Dependencies
          </h2>
          <p className="text-sm text-gray-500 mb-6">
            The imported agent declares the following Python packages. They will
            be installed automatically into the server virtual environment when
            you click <strong>Write Plugin</strong>.
          </p>

          {hasDeps ? (
            <>
              <div className="rounded-xl bg-slate-900 p-4 mb-4">
                <p className="text-xs text-slate-400 font-mono mb-2">
                  # Will be run automatically on write
                </p>
                <p className="text-sm text-green-400 font-mono break-all">
                  pip install {preview.requirements_to_install.join(' ')}
                </p>
              </div>
              <div className="space-y-1.5">
                {preview.requirements_to_install.map((r, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-50 text-xs font-mono text-gray-700"
                  >
                    <span className="text-gray-400">📦</span>
                    {r}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="rounded-xl bg-green-50 border border-green-200 px-4 py-3 text-sm text-green-700">
              ✓ No additional dependencies declared.
            </div>
          )}
        </div>

        <Warnings warnings={preview.warnings} />

        <div className="flex items-center gap-3">
          <button
            onClick={() => setStep('preview')}
            className="px-5 py-2.5 rounded-xl border border-gray-200 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
          >
            ← Back to preview
          </button>
          <button
            onClick={handleInstall}
            className="flex-1 py-2.5 rounded-xl bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 active:scale-[0.98] transition-all"
          >
            {hasDeps
              ? 'I\'ve installed dependencies — Write plugin →'
              : 'Write plugin →'}
          </button>
        </div>
      </div>
    );
  };

  const renderDone = () => {
    if (!writeResult) return null;
    const v = writeResult.validation;
    const agentUrl = `${getApiBase().replace(/^http/, 'ws')}/ws?agent=${writeResult.plugin_id}`;
    const appUrl = `${typeof window !== 'undefined' ? window.location.origin : 'http://localhost:3000'}/?agent=${writeResult.plugin_id}`;

    return (
      <div className="max-w-2xl mx-auto space-y-4">
        {/* Result header */}
        <div
          className={`rounded-2xl border p-6 ${
            v?.import_ok && v?.smoke_test?.ok
              ? 'bg-green-50 border-green-200'
              : 'bg-amber-50 border-amber-200'
          }`}
        >
          <div className="flex items-start gap-4">
            <div
              className={`text-3xl ${
                v?.import_ok && v?.smoke_test?.ok ? 'text-green-600' : 'text-amber-500'
              }`}
            >
              {v?.import_ok && v?.smoke_test?.ok ? '✓' : '⚠'}
            </div>
            <div>
              <h2
                className={`text-lg font-bold mb-1 ${
                  v?.import_ok && v?.smoke_test?.ok
                    ? 'text-green-800'
                    : 'text-amber-800'
                }`}
              >
                {v?.import_ok && v?.smoke_test?.ok
                  ? 'Plugin installed successfully'
                  : 'Plugin written — manual fixes may be needed'}
              </h2>
              <p
                className={`text-sm ${
                  v?.import_ok ? 'text-green-700' : 'text-amber-700'
                }`}
              >
                Plugin ID:{' '}
                <code className="font-mono font-bold">
                  {writeResult.plugin_id}
                </code>
                {' '}· Restart the server to activate.
              </p>
            </div>
          </div>
        </div>

        {/* Validation results */}
        {v && (
          <div className="bg-white rounded-2xl border border-gray-100 p-6 space-y-4">
            <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">
              Validation
            </h3>
            <div className="flex flex-wrap gap-2">
              <StatusPill ok={v.import_ok} label="Import check" />
              {v.smoke_test && !v.smoke_test.skipped && (
                <>
                  <StatusPill ok={v.smoke_test.ok} label="Smoke test" />
                  {v.smoke_test.ok && (
                    <>
                      <StatusPill ok={v.smoke_test.has_a2ui} label="A2UI rendered" />
                      <StatusPill ok={v.smoke_test.has_voice} label="Voice event" />
                      <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-700">
                        {v.smoke_test.outbox_count} outbox events
                      </span>
                    </>
                  )}
                </>
              )}
              {v.smoke_test?.skipped && (
                <span className="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-500">
                  Smoke test skipped (import failed)
                </span>
              )}
            </div>

            {!v.import_ok && v.import_error && (
              <div className="rounded-xl bg-red-50 border border-red-200 p-4">
                <p className="text-xs font-semibold text-red-700 mb-1">
                  Import error
                </p>
                <pre className="text-xs text-red-800 font-mono whitespace-pre-wrap overflow-auto max-h-32">
                  {v.import_error}
                </pre>
              </div>
            )}

            {v.smoke_test && !v.smoke_test.ok && !v.smoke_test.skipped && (
              <div className="rounded-xl bg-amber-50 border border-amber-200 p-4">
                <p className="text-xs font-semibold text-amber-700 mb-1">
                  Smoke test error
                </p>
                <pre className="text-xs text-amber-800 font-mono whitespace-pre-wrap overflow-auto max-h-32">
                  {v.smoke_test.error}
                </pre>
              </div>
            )}
          </div>
        )}

        {/* Files written */}
        {writeResult.files_written.length > 0 && (
          <div className="bg-white rounded-2xl border border-gray-100 p-5">
            <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
              Files written
            </h3>
            <ul className="space-y-1">
              {writeResult.files_written.map((f) => (
                <li key={f} className="text-xs font-mono text-gray-600 flex gap-2">
                  <span className="text-gray-400">→</span>
                  {f}
                </li>
              ))}
            </ul>
            {writeResult.requirements_to_install.length > 0 && (
              <div className="mt-4 pt-4 border-t border-gray-100">
                <p className="text-xs text-amber-700 font-medium">
                  ⚠ Remember to install dependencies:
                </p>
                <p className="mt-1 text-xs font-mono text-gray-600 bg-gray-50 rounded-lg px-3 py-2">
                  pip install -r app/agent/plugins/
                  {writeResult.plugin_id}/requirements_import.txt
                </p>
              </div>
            )}
          </div>
        )}

        {/* Agent links */}
        <div className="bg-white rounded-2xl border border-gray-100 p-6">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">
            Connect to your new agent
          </h3>
          <div className="space-y-3">
            <div>
              <p className="text-xs text-gray-500 mb-1">Open in assistant UI</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 font-mono text-blue-700 break-all">
                  {appUrl}
                </code>
                <CopyButton text={appUrl} />
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1">WebSocket endpoint</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 font-mono text-gray-600 break-all">
                  {agentUrl}
                </code>
                <CopyButton text={agentUrl} />
              </div>
            </div>
          </div>
        </div>

        <Warnings warnings={writeResult.warnings} />

        <div className="flex gap-3">
          <button
            onClick={reset}
            className="px-5 py-2.5 rounded-xl border border-gray-200 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
          >
            Import another agent
          </button>
          <Link
            href={`/?agent=${writeResult.plugin_id}`}
            className="flex-1 py-2.5 rounded-xl bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-all text-center"
          >
            Open {writeResult.plugin_id} assistant →
          </Link>
        </div>
      </div>
    );
  };

  const renderError = () => (
    <div className="max-w-xl mx-auto">
      <div className="bg-red-50 border border-red-200 rounded-2xl p-8">
        <h2 className="text-lg font-bold text-red-800 mb-2">Import failed</h2>
        <p className="text-sm text-red-700 mb-4">
          The import pipeline encountered an error. Check the details below,
          then fix and try again.
        </p>
        <pre className="text-xs font-mono text-red-900 bg-red-100 rounded-xl p-4 whitespace-pre-wrap overflow-auto max-h-48">
          {errorMsg}
        </pre>
        <button
          onClick={reset}
          className="mt-6 w-full py-2.5 rounded-xl bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors"
        >
          ← Try again
        </button>
      </div>
    </div>
  );

  // ── Main render ────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="sticky top-0 z-20 bg-white border-b border-gray-100 shadow-sm">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center gap-4">
          <Link href="/" className="text-gray-400 hover:text-gray-600 transition-colors text-sm">
            ←
          </Link>
          <div className="w-px h-5 bg-gray-200" />
          <div>
            <p className="text-[10px] font-black tracking-widest uppercase text-blue-950">
              Barclays
            </p>
            <p className="text-xs text-gray-500">Agent Import Wizard</p>
          </div>
          <div className="ml-auto">
            <Link
              href="/api/import-agent/plugins"
              target="_blank"
              className="text-xs text-blue-600 hover:underline"
            >
              View registered plugins ↗
            </Link>
          </div>
        </div>
      </header>

      {/* Body */}
      <main className="max-w-5xl mx-auto px-6 py-8">
        <StepIndicator current={step} />

        {step === 'idle' && renderIdle()}
        {step === 'analysing' && (
          <Spinner label="Fetching agent, inspecting schema, generating A2UI design…" />
        )}
        {step === 'preview' && renderPreview()}
        {step === 'deps' && renderDeps()}
        {step === 'installing' && (
          <Spinner label="Writing plugin files, running import check, smoke testing…" />
        )}
        {step === 'done' && renderDone()}
        {step === 'error' && renderError()}
      </main>
    </div>
  );
}

// ── Copy button ────────────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="shrink-0 px-3 py-2 rounded-lg text-xs font-medium bg-gray-100 text-gray-600 hover:bg-gray-200 transition-colors"
    >
      {copied ? '✓' : 'Copy'}
    </button>
  );
}
