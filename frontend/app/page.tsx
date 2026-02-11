"use client";

import { useState, useRef, useEffect } from "react";
import { startClone, getClones, getPreviewUrl, resolveApiUrl, type CloneHistoryItem, type CloneFile, type CloneEvent } from "@/lib/api";
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
  Search,
  Camera,
  Cpu,
  Rocket,
  CheckCircle2,
  AlertCircle,
  FileCode2,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";

type CloneStatus = "idle" | "scraping" | "generating" | "deploying" | "done" | "error";

type LogEntry = MessageLogEntry | FileLogEntry | ScreenshotLogEntry;

interface MessageLogEntry {
  kind: "message";
  id: number;
  icon: "search" | "camera" | "cpu" | "rocket" | "done" | "error";
  message: string;
  timestamp: Date;
  status: "active" | "done" | "error";
}

interface FileLogEntry {
  kind: "file";
  id: number;
  file: string;
  lines: number;
  timestamp: Date;
}

interface ScreenshotLogEntry {
  kind: "screenshot";
  id: number;
  src: string;
  timestamp: Date;
}

const ICON_MAP = {
  search: Search,
  camera: Camera,
  cpu: Cpu,
  rocket: Rocket,
  done: CheckCircle2,
  error: AlertCircle,
};

function getIconForStatus(status: string, message: string): MessageLogEntry["icon"] {
  if (status === "error") return "error";
  if (status === "done") return "done";
  if (status === "deploying") return "rocket";
  if (status === "generating") return "cpu";
  if (message.toLowerCase().includes("screenshot")) return "camera";
  return "search";
}

export default function Home() {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<CloneStatus>("idle");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [generatedFiles, setGeneratedFiles] = useState<CloneFile[]>([]);
  const [showCode, setShowCode] = useState(false);
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<CloneHistoryItem[]>([]);
  const [copied, setCopied] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [expandedScreenshot, setExpandedScreenshot] = useState<string | null>(null);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logIdRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getClones().then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    if (status === "idle") inputRef.current?.focus();
  }, [status]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logEntries]);

  // Elapsed timer — runs while cloning is in progress
  useEffect(() => {
    if (status === "scraping" || status === "generating" || status === "deploying") {
      if (!timerRef.current) {
        setElapsed(0);
        timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
      }
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [status]);

  const addMessageLog = (icon: MessageLogEntry["icon"], message: string, logStatus: MessageLogEntry["status"] = "active") => {
    const id = ++logIdRef.current;
    setLogEntries((prev) => {
      const updated = prev.map((e) =>
        e.kind === "message" && e.status === "active" ? { ...e, status: "done" as const } : e
      );
      return [...updated, { kind: "message" as const, id, icon, message, timestamp: new Date(), status: logStatus }];
    });
  };

  const addFileLog = (file: string, lines: number) => {
    const id = ++logIdRef.current;
    setLogEntries((prev) => [
      ...prev,
      { kind: "file" as const, id, file, lines, timestamp: new Date() },
    ]);
  };

  const handleClone = async () => {
    if (!url.trim()) return;

    let targetUrl = url.trim();
    if (!/^https?:\/\//i.test(targetUrl)) {
      targetUrl = "https://" + targetUrl;
    }

    setStatus("scraping");
    setError(null);
    setPreviewUrl(null);
    setGeneratedFiles([]);
    setShowCode(false);
    setActiveFile(null);
    setLogEntries([]);
    logIdRef.current = 0;

    addMessageLog("search", "Starting clone process...");

    try {
      await startClone(targetUrl, (data: CloneEvent) => {
        if (data.status === "file_write") {
          // File operation card
          if (data.file && data.lines) {
            addFileLog(data.file, data.lines);
          }
        } else if (data.status === "screenshot") {
          // Show the captured screenshot in the activity log
          if (data.screenshot) {
            const id = ++logIdRef.current;
            setLogEntries((prev) => {
              const updated = prev.map((e) =>
                e.kind === "message" && e.status === "active" ? { ...e, status: "done" as const } : e
              );
              return [...updated, { kind: "screenshot" as const, id, src: `data:image/png;base64,${data.screenshot}`, timestamp: new Date() }];
            });
          }
          addMessageLog("camera", "Screenshot captured", "done");
        } else if (data.status === "file_upload") {
          // File uploaded to sandbox — show in activity log
          addMessageLog("rocket", data.message || "Uploading file...");
        } else if (data.status === "sandbox_ready") {
          // Sandbox is ready with placeholder — show iframe immediately
          if (data.preview_url) {
            setPreviewUrl(resolveApiUrl(data.preview_url));
          }
          addMessageLog("rocket", "Sandbox ready — loading preview...", "done");
        } else if (data.status === "scraping") {
          setStatus("scraping");
          addMessageLog(getIconForStatus(data.status, data.message || ""), data.message || "");
        } else if (data.status === "generating") {
          setStatus("generating");
          addMessageLog(getIconForStatus(data.status, data.message || ""), data.message || "");
        } else if (data.status === "deploying") {
          setStatus("deploying");
          addMessageLog("rocket", data.message || "Deploying...");
        } else if (data.status === "done") {
          setStatus("done");
          addMessageLog("done", "Clone complete!", "done");
          if (data.files && data.files.length > 0) {
            setGeneratedFiles(data.files);
            setActiveFile(data.files[0].path);
          }
          if (data.preview_url) setPreviewUrl(resolveApiUrl(data.preview_url));
          getClones().then(setHistory).catch(() => {});
        } else if (data.status === "error") {
          setStatus("error");
          setError(data.message || "Unknown error");
          addMessageLog("error", data.message || "Unknown error", "error");
        }
      });
    } catch (err) {
      setStatus("error");
      const msg = err instanceof Error ? err.message : "Something went wrong";
      setError(msg);
      addMessageLog("error", msg, "error");
    }
  };

  const handleCopyCode = async () => {
    const file = generatedFiles.find((f) => f.path === activeFile);
    if (!file) return;
    await navigator.clipboard.writeText(file.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleHistoryClick = (item: CloneHistoryItem) => {
    setPreviewUrl(getPreviewUrl(item.id));
    setStatus("done");
    setUrl(item.url);
    setGeneratedFiles([]);
    setLogEntries([{ kind: "message", id: 1, icon: "done", message: "Loaded from history", timestamp: new Date(), status: "done" }]);
  };

  const reset = () => {
    setStatus("idle");
    setUrl("");
    setPreviewUrl(null);
    setGeneratedFiles([]);
    setError(null);
    setShowCode(false);
    setActiveFile(null);
    setLogEntries([]);
  };

  const isLoading = status === "scraping" || status === "generating" || status === "deploying";
  const hasResult = status === "done" && (previewUrl || generatedFiles.length > 0);
  const showWorkspace = isLoading || hasResult || status === "error";

  const currentFileContent = generatedFiles.find((f) => f.path === activeFile)?.content || "";

  // ── Idle: centered landing ──
  if (!showWorkspace) {
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
              Paste a URL and get an AI-generated Next.js replica in seconds
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

  // ── Workspace: activity log left + preview/code right ──
  return (
    <main className="h-screen flex flex-col">
      {/* Top bar */}
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

        {hasResult && (
          <>
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

            {showCode && activeFile && (
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
          </>
        )}

        <button
          onClick={reset}
          className="bg-primary text-primary-foreground rounded-lg px-4 py-1.5 text-sm font-medium hover:bg-primary/90 transition-all flex items-center gap-2"
        >
          New clone
        </button>
      </div>

      {/* Main workspace */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left: Activity log */}
        {!sidebarOpen && (
          <button
            onClick={() => setSidebarOpen(true)}
            className="absolute left-2 top-14 z-20 p-1.5 rounded-lg bg-card border border-border shadow-sm hover:bg-secondary transition-colors"
            title="Show activity"
          >
            <PanelLeftOpen className="w-4 h-4 text-muted-foreground" />
          </button>
        )}
        <div className={`shrink-0 border-r bg-card flex flex-col z-10 transition-all duration-200 ${sidebarOpen ? "w-80" : "w-0 overflow-hidden border-r-0"}`}>
          <div className="px-4 py-3 border-b flex items-center justify-between">
            <h2 className="text-sm font-semibold flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-primary" />
              Activity
            </h2>
            <button
              onClick={() => setSidebarOpen(false)}
              className="p-1 rounded-md hover:bg-secondary transition-colors"
              title="Hide activity"
            >
              <PanelLeftClose className="w-4 h-4 text-muted-foreground" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            <div className="space-y-1">
              {logEntries.map((entry) => {
                if (entry.kind === "screenshot") {
                  return (
                    <div key={entry.id} className="py-2 px-2 rounded-lg">
                      <p className="text-xs text-muted-foreground mb-1.5 flex items-center gap-1.5">
                        <Camera className="w-3 h-3" /> Screenshot captured
                      </p>
                      <img
                        src={entry.src}
                        alt="Website screenshot"
                        className="w-full rounded-md border border-border/50 cursor-pointer hover:opacity-80 transition-opacity"
                        onClick={() => setExpandedScreenshot(entry.src)}
                      />
                    </div>
                  );
                }

                if (entry.kind === "file") {
                  // File operation card
                  return (
                    <div
                      key={entry.id}
                      className="flex items-center gap-3 py-2 px-3 rounded-lg bg-secondary/50 border border-border/50"
                    >
                      <FileCode2 className="w-4 h-4 text-muted-foreground shrink-0" />
                      <span className="text-sm text-foreground truncate flex-1 font-mono">
                        {entry.file}
                      </span>
                      <span className="text-xs font-mono text-green-400 shrink-0">
                        +{entry.lines}
                      </span>
                    </div>
                  );
                }

                // Message log entry
                const Icon = ICON_MAP[entry.icon];
                return (
                  <div
                    key={entry.id}
                    className={`flex items-start gap-3 py-2 px-2 rounded-lg transition-colors ${
                      entry.status === "active" ? "bg-primary/5" : ""
                    }`}
                  >
                    <div className={`mt-0.5 shrink-0 ${
                      entry.status === "active"
                        ? "text-primary"
                        : entry.status === "error"
                        ? "text-destructive"
                        : "text-muted-foreground"
                    }`}>
                      {entry.status === "active" ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Icon className="w-4 h-4" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm leading-snug ${
                        entry.status === "active"
                          ? "text-foreground"
                          : entry.status === "error"
                          ? "text-destructive"
                          : "text-muted-foreground"
                      }`}>
                        {entry.message}
                      </p>
                      <p className="text-xs text-muted-foreground/50 mt-0.5">
                        {entry.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                      </p>
                    </div>
                    {entry.status === "done" && entry.icon !== "done" && (
                      <CheckCircle2 className="w-3.5 h-3.5 text-green-500 mt-0.5 shrink-0" />
                    )}
                  </div>
                );
              })}
              <div ref={logEndRef} />
            </div>

            {isLoading && (
              <div className="mt-4 pt-4 border-t border-border/50">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                  Working... {elapsed > 0 && `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right: Preview / Code panel */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {isLoading && !previewUrl && (
            <div className="flex-1 flex items-center justify-center bg-muted/20">
              <div className="text-center">
                <div className="relative inline-flex items-center justify-center mb-6">
                  <div className="absolute w-20 h-20 rounded-full bg-primary/20 animate-pulse-ring" />
                  <div className="absolute w-14 h-14 rounded-full bg-primary/10 animate-pulse" />
                  <Sparkles className="relative w-8 h-8 text-primary" />
                </div>
                <p className="text-muted-foreground text-sm">
                  Building your clone...
                </p>
              </div>
            </div>
          )}

          {/* Live preview — shown as soon as sandbox is ready, even while still loading */}
          {isLoading && previewUrl && (
            <div className="flex-1 relative">
              <iframe
                src={previewUrl}
                className="absolute inset-0 w-full h-full border-0"
                title="Cloned website preview"
                sandbox="allow-scripts allow-same-origin"
              />
            </div>
          )}

          {status === "error" && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center max-w-sm">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-destructive/10 mb-4">
                  <X className="w-7 h-7 text-destructive" />
                </div>
                <h2 className="text-xl font-semibold mb-2">Clone failed</h2>
                <p className="text-sm text-muted-foreground mb-4">{error}</p>
                <button
                  onClick={reset}
                  className="bg-card border border-border rounded-xl px-5 py-2 text-sm font-medium hover:bg-secondary transition-colors"
                >
                  Try again
                </button>
              </div>
            </div>
          )}

          {/* Preview mode */}
          {hasResult && !showCode && previewUrl && (
            <div className="flex-1 relative">
              <iframe
                src={previewUrl}
                className="absolute inset-0 w-full h-full border-0"
                title="Cloned website preview"
                sandbox="allow-scripts allow-same-origin"
              />
            </div>
          )}
          {hasResult && !showCode && !previewUrl && generatedFiles.length > 0 && (
            <div className="flex-1 flex items-center justify-center bg-muted/20">
              <div className="text-center">
                <FileCode2 className="w-10 h-10 text-muted-foreground mx-auto mb-3" />
                <p className="text-muted-foreground text-sm mb-2">No sandbox preview available</p>
                <button
                  onClick={() => setShowCode(true)}
                  className="text-primary text-sm hover:underline"
                >
                  View generated code instead
                </button>
              </div>
            </div>
          )}

          {/* Code mode: file tabs + content */}
          {hasResult && showCode && generatedFiles.length > 0 && (
            <div className="flex-1 flex overflow-hidden">
              {/* File tree sidebar */}
              <div className="w-56 shrink-0 border-r bg-[hsl(220,13%,5%)] overflow-y-auto">
                <div className="px-3 py-2 border-b border-border/50">
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Files</p>
                </div>
                <div className="py-1">
                  {generatedFiles.map((file) => (
                    <button
                      key={file.path}
                      onClick={() => setActiveFile(file.path)}
                      className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors ${
                        activeFile === file.path
                          ? "bg-primary/10 text-foreground"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary/30"
                      }`}
                    >
                      <FileCode2 className="w-3.5 h-3.5 shrink-0" />
                      <span className="truncate font-mono text-xs">{file.path}</span>
                      <ChevronRight className={`w-3 h-3 ml-auto shrink-0 transition-transform ${
                        activeFile === file.path ? "rotate-90" : ""
                      }`} />
                    </button>
                  ))}
                </div>
              </div>

              {/* File content */}
              <div className="flex-1 overflow-auto bg-[hsl(220,13%,3%)]">
                {activeFile && (
                  <div>
                    <div className="sticky top-0 z-10 flex items-center gap-2 px-4 py-2 bg-[hsl(220,13%,5%)] border-b border-border/50">
                      <FileCode2 className="w-3.5 h-3.5 text-muted-foreground" />
                      <span className="text-xs font-mono text-muted-foreground">{activeFile}</span>
                      <span className="text-xs font-mono text-green-400 ml-auto">
                        {generatedFiles.find(f => f.path === activeFile)?.lines || 0} lines
                      </span>
                    </div>
                    <pre className="p-4 text-sm font-mono leading-relaxed whitespace-pre-wrap">
                      <code className="text-muted-foreground">{currentFileContent}</code>
                    </pre>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Screenshot lightbox */}
      {expandedScreenshot && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm cursor-pointer"
          onClick={() => setExpandedScreenshot(null)}
        >
          <button
            onClick={() => setExpandedScreenshot(null)}
            className="absolute top-4 right-4 p-2 rounded-full bg-white/10 hover:bg-white/20 transition-colors"
          >
            <X className="w-5 h-5 text-white" />
          </button>
          <img
            src={expandedScreenshot}
            alt="Screenshot expanded"
            className="max-w-[90vw] max-h-[90vh] rounded-lg shadow-2xl object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </main>
  );
}
