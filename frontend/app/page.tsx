"use client";

import { useState, useRef, useEffect } from "react";
import { startClone, getClones, getPreviewUrl, type CloneHistoryItem } from "@/lib/api";
import {
  Globe,
  Loader2,
  Code2,
  Eye,
  ArrowRight,
  ExternalLink,
  Copy,
  Check,
  X,
  Clock,
  Sparkles,
} from "lucide-react";

type CloneStatus = "idle" | "scraping" | "generating" | "deploying" | "done" | "error";

const STEPS = [
  { key: "scraping", label: "Scraping", icon: Globe },
  { key: "generating", label: "Generating", icon: Sparkles },
  { key: "deploying", label: "Deploying", icon: ArrowRight },
] as const;

export default function Home() {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<CloneStatus>("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [generatedHtml, setGeneratedHtml] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<CloneHistoryItem[]>([]);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getClones().then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    if (status === "idle") inputRef.current?.focus();
  }, [status]);

  const handleClone = async () => {
    if (!url.trim()) return;

    let targetUrl = url.trim();
    if (!/^https?:\/\//i.test(targetUrl)) {
      targetUrl = "https://" + targetUrl;
    }

    setStatus("scraping");
    setStatusMessage("Fetching website...");
    setError(null);
    setPreviewUrl(null);
    setGeneratedHtml(null);
    setShowCode(false);

    try {
      await startClone(targetUrl, (data) => {
        if (data.status === "scraping") {
          setStatus("scraping");
          setStatusMessage(data.message);
        } else if (data.status === "generating") {
          setStatus("generating");
          setStatusMessage(data.message);
        } else if (data.status === "deploying") {
          setStatus("deploying");
          setStatusMessage(data.message);
        } else if (data.status === "done") {
          setStatus("done");
          setStatusMessage("");
          if (data.html) setGeneratedHtml(data.html);
          if (data.preview_url) setPreviewUrl(data.preview_url);
          getClones().then(setHistory).catch(() => {});
        } else if (data.status === "error") {
          setStatus("error");
          setError(data.message);
        }
      });
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Something went wrong");
    }
  };

  const handleCopyCode = async () => {
    if (!generatedHtml) return;
    await navigator.clipboard.writeText(generatedHtml);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleHistoryClick = (item: CloneHistoryItem) => {
    setPreviewUrl(getPreviewUrl(item.id));
    setStatus("done");
    setUrl(item.url);
  };

  const reset = () => {
    setStatus("idle");
    setUrl("");
    setPreviewUrl(null);
    setGeneratedHtml(null);
    setError(null);
    setShowCode(false);
  };

  const isLoading = status === "scraping" || status === "generating" || status === "deploying";
  const hasResult = status === "done" && (previewUrl || generatedHtml);
  const currentStepIndex = STEPS.findIndex((s) => s.key === status);

  // ── Idle: centered ChatGPT-style landing ──
  if (status === "idle") {
    return (
      <main className="min-h-screen flex flex-col">
        <div className="flex-1 flex flex-col items-center justify-center px-4">
          <div className="mb-8 text-center">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-primary/10 mb-6">
              <Globe className="w-8 h-8 text-primary" />
            </div>
            <h1 className="text-4xl font-bold tracking-tight mb-3">
              Clone any website
            </h1>
            <p className="text-muted-foreground text-lg max-w-md">
              Paste a URL and get an AI-generated replica in seconds
            </p>
          </div>

          <form
            onSubmit={(e) => { e.preventDefault(); handleClone(); }}
            className="w-full max-w-xl"
          >
            <div className="relative flex items-center bg-card border border-border rounded-2xl px-4 py-2 focus-within:ring-2 focus-within:ring-primary/50 focus-within:border-primary/50 transition-all">
              <Globe className="w-5 h-5 text-muted-foreground shrink-0" />
              <input
                ref={inputRef}
                type="text"
                placeholder="https://example.com"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className="flex-1 bg-transparent border-0 outline-none px-3 py-2 text-base placeholder:text-muted-foreground"
              />
              <button
                type="submit"
                disabled={!url.trim()}
                className="shrink-0 bg-primary text-primary-foreground rounded-xl px-4 py-2 text-sm font-medium hover:bg-primary/90 disabled:opacity-30 disabled:cursor-not-allowed transition-all flex items-center gap-2"
              >
                Clone
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </form>

          {/* Recent clones */}
          {history.length > 0 && (
            <div className="mt-12 w-full max-w-xl">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <Clock className="w-3 h-3" /> Recent
              </p>
              <div className="flex flex-wrap gap-2">
                {history.slice(0, 6).map((item) => (
                  <button
                    key={item.id}
                    onClick={() => handleHistoryClick(item)}
                    className="text-sm px-3 py-1.5 rounded-lg bg-secondary hover:bg-secondary/80 text-secondary-foreground truncate max-w-[220px] transition-colors"
                  >
                    {item.url.replace(/^https?:\/\//, "")}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>
    );
  }

  // ── Loading: progress view ──
  if (isLoading) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center px-4">
        <div className="text-center max-w-md">
          {/* Animated orb */}
          <div className="relative inline-flex items-center justify-center mb-8">
            <div className="absolute w-20 h-20 rounded-full bg-primary/20 animate-pulse-ring" />
            <div className="absolute w-14 h-14 rounded-full bg-primary/10 animate-pulse" />
            <Sparkles className="relative w-8 h-8 text-primary" />
          </div>

          <h2 className="text-2xl font-semibold mb-2">
            {status === "scraping" && "Analyzing website..."}
            {status === "generating" && "Generating clone..."}
            {status === "deploying" && "Almost there..."}
          </h2>
          <p className="text-muted-foreground mb-8">{statusMessage}</p>

          {/* Step indicators */}
          <div className="flex items-center justify-center gap-3">
            {STEPS.map((step, i) => {
              const Icon = step.icon;
              const isActive = i === currentStepIndex;
              const isDone = i < currentStepIndex;
              return (
                <div key={step.key} className="flex items-center gap-3">
                  {i > 0 && (
                    <div className={`w-8 h-px ${isDone ? "bg-primary" : "bg-border"}`} />
                  )}
                  <div
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm transition-all ${
                      isActive
                        ? "bg-primary/15 text-primary"
                        : isDone
                        ? "text-primary/60"
                        : "text-muted-foreground/40"
                    }`}
                  >
                    {isActive ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Icon className="w-3.5 h-3.5" />
                    )}
                    {step.label}
                  </div>
                </div>
              );
            })}
          </div>

          <p className="text-xs text-muted-foreground/50 mt-8">
            Cloning {url.replace(/^https?:\/\//, "")}
          </p>
        </div>
      </main>
    );
  }

  // ── Error state ──
  if (status === "error") {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center px-4">
        <div className="text-center max-w-md">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-destructive/10 mb-6">
            <X className="w-8 h-8 text-destructive" />
          </div>
          <h2 className="text-2xl font-semibold mb-2">Clone failed</h2>
          <p className="text-muted-foreground mb-6">{error}</p>
          <button
            onClick={reset}
            className="bg-card border border-border rounded-xl px-6 py-2.5 text-sm font-medium hover:bg-secondary transition-colors"
          >
            Try again
          </button>
        </div>
      </main>
    );
  }

  // ── Result: full-screen preview ──
  if (hasResult) {
    return (
      <main className="h-screen flex flex-col">
        {/* Toolbar */}
        <div className="shrink-0 flex items-center gap-2 px-4 py-2.5 border-b bg-card/80 backdrop-blur-sm">
          <button
            onClick={reset}
            className="flex items-center gap-2 text-sm font-medium hover:text-primary transition-colors mr-2"
          >
            <Globe className="w-4 h-4" />
            Clonr
          </button>

          <div className="w-px h-5 bg-border" />

          <span className="text-sm text-muted-foreground truncate max-w-[300px] ml-2">
            {url.replace(/^https?:\/\//, "")}
          </span>

          <div className="flex-1" />

          {/* Preview / Code toggle */}
          <div className="flex items-center bg-secondary rounded-lg p-0.5">
            <button
              onClick={() => setShowCode(false)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-all ${
                !showCode
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Eye className="w-3.5 h-3.5" />
              Preview
            </button>
            <button
              onClick={() => setShowCode(true)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-all ${
                showCode
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Code2 className="w-3.5 h-3.5" />
              Code
            </button>
          </div>

          {showCode && generatedHtml && (
            <button
              onClick={handleCopyCode}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-secondary transition-all"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? "Copied" : "Copy"}
            </button>
          )}

          {previewUrl && (
            <a
              href={previewUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-secondary transition-all"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}

          <button
            onClick={reset}
            className="bg-primary text-primary-foreground rounded-lg px-4 py-1.5 text-sm font-medium hover:bg-primary/90 transition-all flex items-center gap-2"
          >
            New clone
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden">
          {!showCode && generatedHtml && (
            <iframe
              srcDoc={generatedHtml}
              className="w-full h-full border-0"
              title="Cloned website preview"
              sandbox="allow-scripts"
            />
          )}
          {!showCode && !generatedHtml && previewUrl && (
            <iframe
              src={previewUrl}
              className="w-full h-full border-0"
              title="Cloned website preview"
              sandbox="allow-scripts allow-same-origin"
            />
          )}
          {showCode && generatedHtml && (
            <div className="h-full overflow-auto bg-[hsl(220,13%,3%)]">
              <pre className="p-6 text-sm font-mono text-muted-foreground leading-relaxed whitespace-pre-wrap">
                <code>{generatedHtml}</code>
              </pre>
            </div>
          )}
        </div>
      </main>
    );
  }

  return null;
}
