import React, { useMemo, useState, useEffect, useRef } from 'react';
import Editor from '@monaco-editor/react';
import { marked } from 'marked';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.min.css';

const SUPABASE_URL = 'https://cycgenxaotnkzqhrkhlf.supabase.co';
const SUPABASE_KEY = 'PASTE_YOUR_SERVICE_ROLE_KEY_HERE';

const LANGUAGES = ['C', 'Python', 'JavaScript', 'Java'];

marked.setOptions({
  highlight: (code, lang) => {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
    return hljs.highlight(code, { language }).value;
  },
  langPrefix: 'hljs language-',
  breaks: false,
  gfm: true,
});

// Loads marked + highlight.js from CDN once
function useMarkdownRenderer() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (window.__markdownReady) { setReady(true); return; }

    const loadScript = (src) =>
      new Promise((resolve, reject) => {
        if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
        const s = document.createElement('script');
        s.src = src;
        s.onload = resolve;
        s.onerror = reject;
        document.head.appendChild(s);
      });

    const loadLink = (href) => {
      if (document.querySelector(`link[href="${href}"]`)) return;
      const l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = href;
      document.head.appendChild(l);
    };

    Promise.all([
      loadScript('https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js'),
      loadScript('https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js'),
    ]).then(() => {
      loadLink('https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css');

      window.marked.setOptions({
        highlight: (code, lang) => {
          const language = window.hljs.getLanguage(lang) ? lang : 'plaintext';
          return window.hljs.highlight(code, { language }).value;
        },
        langPrefix: 'hljs language-',
        breaks: false,
        gfm: true,
      });

      window.__markdownReady = true;
      setReady(true);
    });
  }, []);

  const render = (markdown) => {
    if (!ready || !window.marked) return '';
    const stripped = markdown.replace(/^#+ Sources[\s\S]*$/m, '').trimEnd();
    return window.marked.parse(stripped);
  };

  return { ready, render };
}

function DebugResult({ markdown }) {
  const ref = useRef(null);

  const html = useMemo(() => {
    if (!markdown) return '';
    const stripped = markdown.replace(/^#+ Sources[\s\S]*$/m, '').trimEnd();
    return marked.parse(stripped);
  }, [markdown]);

  // After inject, wrap every <pre> to add a language label bar
  useEffect(() => {
    if (!ref.current) return;
    ref.current.querySelectorAll('pre').forEach((pre) => {
      if (pre.dataset.wrapped) return;
      pre.dataset.wrapped = '1';

      const code = pre.querySelector('code');
      const langMatch = code?.className?.match(/language-(\w+)/);
      const lang = langMatch?.[1] ?? 'code';

      const bar = document.createElement('div');
      bar.className = 'code-lang-bar';
      bar.textContent = lang;
      pre.insertBefore(bar, pre.firstChild);
    });
  }, [html]);

  // No more loading states needed, it renders instantly
  return (
    <div
      ref={ref}
      className="debug-result"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export default function App() {
  const [view, setView] = useState('form');
  const [language, setLanguage] = useState('C');
  const [errorMessage, setErrorMessage] = useState('');
  const [codeSnippet, setCodeSnippet] = useState('');
  const [result, setResult] = useState('');
  const [sources, setSources] = useState([]);
  const [requestError, setRequestError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // Map display languages to Monaco editor language identifiers
  const monacoLanguage = useMemo(() => {
    switch (language) {
      case 'C': return 'c';
      case 'Python': return 'python';
      case 'JavaScript': return 'javascript';
      case 'Java': return 'java';
      default: return 'plaintext';
    }
  }, [language]);

  const canSubmit = useMemo(
    () => errorMessage.trim().length > 0 && !isLoading,
    [errorMessage, isLoading]
  );

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;

    setIsLoading(true);
    setRequestError('');
    setResult('');
    setSources([]);

    try {
      const res = await fetch(`${SUPABASE_URL}/functions/v1/generate-fix`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${SUPABASE_KEY}`,
          apikey: SUPABASE_KEY,
        },
        body: JSON.stringify({
          errorMessage: errorMessage.trim(),
          codeSnippet: codeSnippet.trim(),
          language,
        }),
      });

      const data = await res.json();
      if (!data?.success) throw new Error(data?.error || 'No solution was returned.');

      setResult(data.solution || '');
      setSources(Array.isArray(data.sources) ? data.sources.filter(Boolean) : []);
      setView('result');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      setRequestError(err.message || 'Something went wrong.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = () => {
    setView('form');
    setResult('');
    setSources([]);
    setRequestError('');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const loadSample = () => {
    setLanguage('Python');
    setErrorMessage(`AttributeError: 'InstallRequirement' object has no attribute 'use_pep517'`);
    setCodeSnippet(`pip install -U 'pip<25.3'`);
  };

  return (
    <>
      <style>{`
        .debug-result h1 {
          font-size: 1.125rem;
          font-weight: 500;
          color: #0f172a;
          margin: 0 0 1rem;
          padding-bottom: 0.75rem;
          border-bottom: 1px solid #e2e8f0;
        }
        .debug-result h2 {
          font-size: 0.9375rem;
          font-weight: 600;
          color: #0f172a;
          margin: 1.75rem 0 0.5rem;
          letter-spacing: -0.01em;
        }
        .debug-result h2:first-child { margin-top: 0; }
        .debug-result p {
          font-size: 0.875rem;
          line-height: 1.75;
          color: #475569;
          margin-bottom: 0.75rem;
        }
        .debug-result ul, .debug-result ol {
          padding-left: 1.35rem;
          margin-bottom: 1rem;
        }
        .debug-result ul { list-style-type: disc; }
        .debug-result ol { list-style-type: decimal; }
        .debug-result li {
          font-size: 0.875rem;
          line-height: 1.75;
          color: #475569;
          margin-bottom: 0.3rem;
        }
        .debug-result li::marker { color: #94a3b8; }
        .debug-result code {
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
          font-size: 0.8em;
          background: #f1f5f9;
          border: 1px solid #e2e8f0;
          border-radius: 4px;
          padding: 0.15em 0.45em;
          color: #0f172a;
        }
        .debug-result pre {
          background: #0d1117;
          border-radius: 12px;
          border: 1px solid #1e293b;
          margin: 1.25rem 0;
          overflow: hidden;
        }
        .debug-result pre .code-lang-bar {
          padding: 7px 14px;
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
          font-size: 11px;
          font-weight: 500;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: rgba(255,255,255,0.3);
          border-bottom: 1px solid rgba(255,255,255,0.06);
          background: rgba(255,255,255,0.03);
        }
        .debug-result pre code {
          display: block;
          padding: 1rem 1.125rem;
          font-size: 0.8125rem;
          line-height: 1.65;
          color: #e2e8f0;
          background: none;
          border: none;
          border-radius: 0;
          overflow-x: auto;
          white-space: pre;
        }
        .debug-result strong { font-weight: 600; color: #0f172a; }
        .debug-result em { font-style: italic; }
        .debug-result a { color: #2563eb; text-decoration: underline; text-underline-offset: 2px; }
        .debug-result a:hover { color: #1d4ed8; }
        .debug-result hr { border: none; border-top: 1px solid #e2e8f0; margin: 1.5rem 0; }
      `}</style>

      <div className="min-h-screen bg-slate-100 text-slate-900">
        {/* Header */}
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
          <div className="flex h-14 w-full items-center justify-between px-8">
            <button onClick={handleReset} className="text-left">
              <div className="text-[15px] font-medium text-slate-950">RAG Debugging Assistant</div>
            </button>
            {view === 'result' && (
              <button
                onClick={handleReset}
                className="rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50"
              >
                New query
              </button>
            )}
          </div>
        </header>

        <main className="w-full px-8 py-8">
          {/* Form */}
          {view === 'form' && (
            <div className="grid gap-8 lg:grid-cols-[1fr_1.2fr] w-full">
              <section className="pt-1">
                <p className="mt-3 text-sm leading-7 text-slate-500">
                  Paste your error message, add the failing code if you have it, and get a
                  structured analysis with a suggested fix.
                </p>
                <button
                  type="button"
                  onClick={loadSample}
                  className="mt-6 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-600 transition hover:bg-slate-50"
                >
                  Load sample
                </button>
              </section>

              <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <form onSubmit={handleSubmit} className="space-y-4">
                  <div>
                    <label className="mb-2 block text-[11px] font-medium uppercase tracking-widest text-slate-400">
                      Language
                    </label>
                    <div className="grid grid-cols-4 gap-2">
                      {LANGUAGES.map((item) => (
                        <button
                          key={item}
                          type="button"
                          onClick={() => setLanguage(item)}
                          className={`rounded-lg border px-3 py-2.5 text-sm font-medium transition ${
                            language === item
                              ? 'border-slate-900 bg-slate-900 text-white'
                              : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                          }`}
                        >
                          {item}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="mb-2 block text-[11px] font-medium uppercase tracking-widest text-slate-400">
                      Error message
                    </label>
                    <textarea
                      required
                      value={errorMessage}
                      onChange={(e) => setErrorMessage(e.target.value)}
                      rows={5}
                      className="w-full resize-none rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 font-mono text-[13px] text-slate-900 outline-none transition placeholder:text-slate-400 focus:border-slate-300 focus:bg-white"
                    />
                  </div>

                  <div>
                    <label className="mb-2 block text-[11px] font-medium uppercase tracking-widest text-slate-400">
                      Code context{' '}
                      <span className="normal-case tracking-normal font-normal text-slate-400">
                        optional
                      </span>
                    </label>
                    <div className="h-56 w-full overflow-hidden rounded-lg border border-slate-200 focus-within:border-slate-300 bg-white">
                      <Editor
                        height="100%"
                        language={monacoLanguage}
                        theme="light"
                        value={codeSnippet}
                        onChange={(value) => setCodeSnippet(value || '')}
                        options={{
                          minimap: { enabled: false },
                          fontSize: 13,
                          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                          scrollBeyondLastLine: false,
                          padding: { top: 12, bottom: 12 },
                          lineNumbersMinChars: 3,
                          renderLineHighlight: 'none',
                          scrollbar: { verticalScrollbarSize: 8 },
                        }}
                      />
                    </div>
                  </div>

                  {requestError && (
                    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
                      {requestError}
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={!canSubmit}
                    className="flex w-full items-center justify-center gap-2 rounded-lg bg-slate-900 px-4 py-3.5 text-sm font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isLoading ? (
                      <>
                        <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/25 border-t-white" />
                        Finding solution
                      </>
                    ) : (
                      'Find solution'
                    )}
                  </button>
                </form>
              </section>
            </div>
          )}

          {/* Result */}
          {view === 'result' && (
            <div className="w-full max-w-none">
              <div className="mb-5 flex items-center justify-between gap-4">
                <h2 className="text-2xl font-medium tracking-tight text-slate-950">
                  Suggested fix
                </h2>
                <button
                  onClick={handleReset}
                  className="shrink-0 rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50"
                >
                  Try another error
                </button>
              </div>

              <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="border-b border-slate-200 px-5 py-3">
                  <span className="text-xs text-slate-400">Generated analysis</span>
                </div>
                <div className="px-6 py-6 sm:px-8 sm:py-7">
                  <DebugResult markdown={result} />
                </div>
              </article>

              {sources.length > 0 && (
                <section className="mt-4 rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
                  <h3 className="mb-3 text-[11px] font-medium uppercase tracking-widest text-slate-400">
                    Sources
                  </h3>
                  <ul className="space-y-2">
                    {sources.map((source, index) => (
                      <li key={`${source}-${index}`}>
                        <a
                          href={source}
                          target="_blank"
                          rel="noreferrer"
                          className="break-all text-sm text-blue-600 underline underline-offset-2 hover:text-blue-700"
                        >
                          {source}
                        </a>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </main>
      </div>
    </>
  );
}