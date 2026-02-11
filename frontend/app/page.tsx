"use client";

import { useState, useRef, useEffect } from "react";
import { Highlight, themes } from "prism-react-renderer";
import { startClone, getClones, getPreviewUrl, resolveApiUrl, type CloneHistoryItem, type CloneFile, type CloneEvent, type ClonePaginatedResponse } from "@/lib/api";
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
  ChevronLeft,
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
  Folder,
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
  const [clonePage, setClonePage] = useState(1);
  const [clonePages, setClonePages] = useState(0);
  const [cloneTotal, setCloneTotal] = useState(0);
  const [copied, setCopied] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [expandedScreenshot, setExpandedScreenshot] = useState<string | null>(null);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const [scaffoldPaths, setScaffoldPaths] = useState<string[]>([]);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [expandedPhases, setExpandedPhases] = useState<Set<string>>(new Set());
  const [elapsed, setElapsed] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logIdRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getClones(clonePage, 30).then((res) => {
      setHistory(res.items);
      setClonePages(res.pages);
      setCloneTotal(res.total);
    }).catch(() => {});
  }, [clonePage]);

  useEffect(() => {
    if (status === "idle") inputRef.current?.focus();
  }, [status]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logEntries]);

  // Elapsed timer — runs continuously while cloning is in progress
  useEffect(() => {
    const isActive = status === "scraping" || status === "generating" || status === "deploying";
    if (isActive && !timerRef.current) {
      timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    } else if (!isActive && timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
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
    setElapsed(0);
    setExpandedPhases(new Set());
    logIdRef.current = 0;
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }

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
          if (data.scaffold_paths) {
            setScaffoldPaths(data.scaffold_paths);
            // Auto-expand folders that contain generated files
            const genPaths = (data.files || []).map((f: CloneFile) => f.path);
            const folders = new Set<string>();
            for (const p of genPaths) {
              const parts = p.split("/");
              for (let i = 1; i < parts.length; i++) {
                folders.add(parts.slice(0, i).join("/"));
              }
            }
            setExpandedFolders(folders);
          }
          if (data.preview_url) setPreviewUrl(resolveApiUrl(data.preview_url));
          getClones(1, 30).then((res) => {
            setHistory(res.items);
            setClonePages(res.pages);
            setCloneTotal(res.total);
            setClonePage(1);
          }).catch(() => {});
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
    // For /api/static/ URLs, use the stored path (has correct storage UUID)
    // For /api/preview/ or missing, use the DB ID which is always correct
    const previewLink = item.preview_url?.includes("/api/static/")
      ? resolveApiUrl(item.preview_url)
      : getPreviewUrl(item.id);
    setPreviewUrl(previewLink);
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

  const timeAgo = (dateStr: string) => {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  };

  const isLoading = status === "scraping" || status === "generating" || status === "deploying";
  const hasResult = status === "done" && (previewUrl || generatedFiles.length > 0);
  const showWorkspace = isLoading || hasResult || status === "error";

  const currentFileContent = generatedFiles.find((f) => f.path === activeFile)?.content || "";

  // ── Build nested file tree from scaffold + generated files ──
  type TreeNode = { name: string; path: string; type: "file" | "folder"; generated: boolean; children: TreeNode[] };

  const fileTree = (() => {
    const root: TreeNode = { name: "", path: "", type: "folder", generated: false, children: [] };
    const genPaths = new Set(generatedFiles.map((f) => f.path));

    function ensureFolder(parts: string[]): TreeNode {
      let current = root;
      let currentPath = "";
      for (const part of parts) {
        currentPath = currentPath ? `${currentPath}/${part}` : part;
        let child = current.children.find((c) => c.name === part && c.type === "folder");
        if (!child) {
          child = { name: part, path: currentPath, type: "folder", generated: false, children: [] };
          current.children.push(child);
        }
        current = child;
      }
      return current;
    }

    function addFile(filePath: string, generated: boolean) {
      const parts = filePath.split("/");
      const fileName = parts.pop()!;
      const folder = ensureFolder(parts);
      if (!folder.children.find((c) => c.name === fileName && c.type === "file")) {
        folder.children.push({ name: fileName, path: filePath, type: "file", generated, children: [] });
      }
    }

    for (const p of scaffoldPaths) addFile(p, false);
    for (const f of generatedFiles) addFile(f.path, true);

    function sortTree(nodes: TreeNode[]) {
      nodes.sort((a, b) => {
        if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      for (const n of nodes) if (n.children.length) sortTree(n.children);
    }
    sortTree(root.children);
    return root.children;
  })();

  const toggleFolder = (path: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });
  };

  const renderTreeNode = (node: TreeNode, depth: number): React.ReactNode => {
    if (node.type === "folder") {
      const isOpen = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <button
            onClick={() => toggleFolder(node.path)}
            className="w-full flex items-center gap-1.5 py-1 text-left text-xs text-muted-foreground hover:text-foreground hover:bg-secondary/30 transition-colors"
            style={{ paddingLeft: depth * 12 + 8 }}
          >
            <ChevronRight className={`w-3 h-3 shrink-0 transition-transform ${isOpen ? "rotate-90" : ""}`} />
            <Folder className="w-3.5 h-3.5 shrink-0 text-blue-400/70" />
            <span className="truncate font-mono">{node.name}</span>
          </button>
          {isOpen && node.children.map((child) => renderTreeNode(child, depth + 1))}
        </div>
      );
    }
    return (
      <button
        key={node.path}
        onClick={() => node.generated && setActiveFile(node.path)}
        className={`w-full flex items-center gap-1.5 py-1 text-left text-xs transition-colors ${
          activeFile === node.path
            ? "bg-primary/10 text-foreground"
            : node.generated
              ? "text-muted-foreground hover:text-foreground hover:bg-secondary/30 cursor-pointer"
              : "text-muted-foreground/40 cursor-default"
        }`}
        style={{ paddingLeft: depth * 12 + 20 }}
      >
        <FileCode2 className="w-3 h-3 shrink-0" />
        <span className="truncate font-mono">{node.name}</span>
        {node.generated && <span className="ml-auto text-[10px] text-green-400/60 shrink-0 pr-2">AI</span>}
      </button>
    );
  };

  // ── Idle: centered landing ──
  if (!showWorkspace) {
    return (
      <main className="min-h-screen flex flex-col">
        <div className={`flex-1 flex flex-col items-center px-4 ${history.length > 0 ? "pt-16" : "justify-center"}`}>
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
            <div className="mt-12 w-full max-w-6xl px-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-4 flex items-center gap-1.5">
                <Clock className="w-3 h-3" /> Recent clones
                {cloneTotal > 0 && <span className="text-muted-foreground/50">({cloneTotal})</span>}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                {history.map((item) => {
                  const domain = item.url.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
                  return (
                    <button
                      key={item.id}
                      onClick={() => handleHistoryClick(item)}
                      className="group text-left p-4 rounded-xl bg-card border border-border hover:border-primary/40 hover:bg-primary/5 transition-all"
                    >
                      <div className="flex items-center gap-2 mb-2">
                        <Globe className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors shrink-0" />
                        <span className="text-sm font-semibold truncate">{domain}</span>
                      </div>
                      <span className="text-xs text-muted-foreground">{timeAgo(item.created_at)}</span>
                    </button>
                  );
                })}
              </div>

              {clonePages > 1 && (
                <div className="flex items-center justify-center gap-1 mt-6">
                  <button
                    onClick={() => setClonePage((p) => Math.max(1, p - 1))}
                    disabled={clonePage <= 1}
                    className="p-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  {Array.from({ length: clonePages }, (_, i) => i + 1)
                    .filter((p) => p === 1 || p === clonePages || Math.abs(p - clonePage) <= 1)
                    .reduce<(number | "...")[]>((acc, p, idx, arr) => {
                      if (idx > 0 && p - (arr[idx - 1]) > 1) acc.push("...");
                      acc.push(p);
                      return acc;
                    }, [])
                    .map((p, idx) =>
                      p === "..." ? (
                        <span key={`ellipsis-${idx}`} className="px-1 text-muted-foreground/50 text-sm">...</span>
                      ) : (
                        <button
                          key={p}
                          onClick={() => setClonePage(p)}
                          className={`min-w-[32px] h-8 rounded-lg text-sm transition-colors ${
                            clonePage === p
                              ? "bg-primary text-primary-foreground font-medium"
                              : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                          }`}
                        >
                          {p}
                        </button>
                      )
                    )}
                  <button
                    onClick={() => setClonePage((p) => Math.min(clonePages, p + 1))}
                    disabled={clonePage >= clonePages}
                    className="p-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              )}
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
          <div className="flex-1 overflow-y-auto px-3 py-2">
            <div className="space-y-1">
              {(() => {
                // Group entries into phases
                type Phase = {
                  id: string;
                  label: string;
                  icon: MessageLogEntry["icon"];
                  status: "active" | "done" | "error";
                  entries: LogEntry[];
                  summary?: string;
                  startTime?: Date;
                  endTime?: Date;
                };

                const phases: Phase[] = [];
                let current: Phase | null = null;

                const phaseMap: Record<string, string> = {
                  search: "scrape",
                  camera: "scrape",
                  cpu: "generate",
                  rocket: "deploy",
                  done: "done",
                  error: "error",
                };

                for (const entry of logEntries) {
                  let phaseKey = "other";
                  if (entry.kind === "message") phaseKey = phaseMap[entry.icon] || "other";
                  else if (entry.kind === "file") phaseKey = "generate";
                  else if (entry.kind === "screenshot") phaseKey = "scrape";

                  if (!current || current.id !== phaseKey) {
                    current = {
                      id: phaseKey,
                      label: phaseKey === "scrape" ? "Scraping website" : phaseKey === "generate" ? "Generating clone" : phaseKey === "deploy" ? "Deploying to sandbox" : phaseKey === "done" ? "Clone complete" : phaseKey === "error" ? "Error" : "Processing",
                      icon: phaseKey === "scrape" ? "search" : phaseKey === "generate" ? "cpu" : phaseKey === "deploy" ? "rocket" : phaseKey === "done" ? "done" : "error",
                      status: "active",
                      entries: [],
                      startTime: entry.timestamp,
                    };
                    phases.push(current);
                  }
                  current.entries.push(entry);
                  current.endTime = entry.timestamp;

                  // Update phase status from last message entry
                  if (entry.kind === "message") {
                    if (entry.status === "done" || entry.status === "error") current.status = entry.status;
                  }
                }

                // Build summaries
                for (const phase of phases) {
                  const files = phase.entries.filter((e): e is FileLogEntry => e.kind === "file");
                  const screenshots = phase.entries.filter((e): e is ScreenshotLogEntry => e.kind === "screenshot");
                  const parts: string[] = [];
                  if (screenshots.length) parts.push(`${screenshots.length} screenshot${screenshots.length > 1 ? "s" : ""}`);
                  if (files.length) {
                    const totalLines = files.reduce((s, f) => s + f.lines, 0);
                    parts.push(`${files.length} files (+${totalLines} lines)`);
                  }
                  if (parts.length) phase.summary = parts.join(", ");
                }

                const togglePhase = (id: string) => {
                  setExpandedPhases((prev) => {
                    const next = new Set(prev);
                    next.has(id) ? next.delete(id) : next.add(id);
                    return next;
                  });
                };

                return phases.map((phase, idx) => {
                  const isOpen = expandedPhases.has(`${phase.id}-${idx}`);
                  const phaseKey = `${phase.id}-${idx}`;
                  const Icon = ICON_MAP[phase.icon];
                  const phaseDuration = phase.startTime && phase.endTime
                    ? Math.round((phase.endTime.getTime() - phase.startTime.getTime()) / 1000)
                    : 0;
                  const isActive = phase.status === "active";
                  const isDone = phase.id === "done";
                  const isError = phase.status === "error";

                  // "Clone complete" gets its own simple row
                  if (isDone) {
                    return (
                      <div key={phaseKey} className="flex items-center gap-2.5 py-2 px-2.5 rounded-lg bg-green-500/5 border border-green-500/20">
                        <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
                        <span className="text-sm font-medium text-green-400 flex-1">Clone complete</span>
                        {elapsed > 0 && <span className="text-[11px] text-muted-foreground/60 tabular-nums">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")} total</span>}
                      </div>
                    );
                  }

                  const files = phase.entries.filter((e): e is FileLogEntry => e.kind === "file");
                  const screenshots = phase.entries.filter((e): e is ScreenshotLogEntry => e.kind === "screenshot");

                  return (
                    <div key={phaseKey} className={`rounded-lg border transition-colors ${isActive ? "bg-primary/5 border-primary/20" : isError ? "bg-destructive/5 border-destructive/20" : "bg-secondary/20 border-border/30"}`}>
                      {/* Phase header — always visible */}
                      <button
                        onClick={() => togglePhase(phaseKey)}
                        className="w-full flex items-center gap-2.5 px-2.5 py-2 text-left"
                      >
                        <ChevronRight className={`w-3 h-3 shrink-0 text-muted-foreground/50 transition-transform ${isOpen ? "rotate-90" : ""}`} />
                        <div className={`shrink-0 ${isActive ? "text-primary" : isError ? "text-destructive" : "text-muted-foreground/60"}`}>
                          {isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Icon className="w-3.5 h-3.5" />}
                        </div>
                        <span className={`text-xs font-medium flex-1 min-w-0 truncate ${isActive ? "text-foreground" : isError ? "text-destructive" : "text-muted-foreground"}`}>
                          {phase.label}
                        </span>
                        {phase.summary && !isActive && (
                          <span className="text-[10px] text-muted-foreground/50 shrink-0">{phase.summary}</span>
                        )}
                        {!isActive && !isError && (
                          <CheckCircle2 className="w-3 h-3 text-green-500/70 shrink-0" />
                        )}
                        {phaseDuration > 0 && !isActive && (
                          <span className="text-[10px] text-muted-foreground/40 shrink-0 tabular-nums">{phaseDuration}s</span>
                        )}
                      </button>

                      {/* Expanded details */}
                      {isOpen && (
                        <div className="px-2.5 pb-2 space-y-0.5">
                          <div className="border-t border-border/20 pt-1.5">
                            {phase.entries.filter((e): e is MessageLogEntry => e.kind === "message").map((entry) => (
                              <div key={entry.id} className="flex items-center gap-2 py-0.5 px-1">
                                <span className="w-1 h-1 rounded-full bg-muted-foreground/30 shrink-0" />
                                <span className="text-[11px] text-muted-foreground/70 truncate flex-1">{entry.message}</span>
                                <span className="text-[10px] text-muted-foreground/30 shrink-0 tabular-nums">
                                  {entry.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                                </span>
                              </div>
                            ))}
                            {screenshots.map((s) => (
                              <div key={s.id} className="flex items-center gap-2 py-0.5 px-1">
                                <Camera className="w-3 h-3 text-muted-foreground/50 shrink-0" />
                                <span className="text-[11px] text-muted-foreground/70 flex-1">Screenshot captured</span>
                                <img
                                  src={s.src}
                                  alt="Screenshot"
                                  className="w-12 h-8 rounded border border-border/50 cursor-pointer hover:opacity-80 transition-opacity object-cover shrink-0"
                                  onClick={() => setExpandedScreenshot(s.src)}
                                />
                              </div>
                            ))}
                            {files.length > 0 && (
                              <div className="mt-1 rounded bg-secondary/30 border border-border/20 overflow-hidden">
                                {files.map((f) => (
                                  <div key={f.id} className="flex items-center gap-2 px-2 py-0.5">
                                    <FileCode2 className="w-3 h-3 text-muted-foreground/50 shrink-0" />
                                    <span className="text-[11px] text-foreground/70 truncate flex-1 font-mono">{f.file}</span>
                                    <span className="text-[10px] font-mono text-green-400/60 shrink-0">+{f.lines}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                });
              })()}
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
                  <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Explorer</p>
                </div>
                <div className="py-1">
                  {fileTree.length > 0
                    ? fileTree.map((node) => renderTreeNode(node, 0))
                    : generatedFiles.map((file) => (
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
                        </button>
                      ))
                  }
                </div>
              </div>

              {/* File content with syntax highlighting */}
              <div className="flex-1 overflow-auto bg-[#0d1117]">
                {activeFile && (
                  <div>
                    <div className="sticky top-0 z-10 flex items-center gap-2 px-4 py-1.5 bg-[#161b22] border-b border-[#30363d]">
                      <FileCode2 className="w-3.5 h-3.5 text-[#8b949e]" />
                      <span className="text-xs font-mono text-[#8b949e]">{activeFile}</span>
                      <span className="text-xs font-mono text-[#3fb950] ml-auto">
                        {generatedFiles.find(f => f.path === activeFile)?.lines || 0} lines
                      </span>
                    </div>
                    <Highlight theme={themes.nightOwl} code={currentFileContent} language="tsx">
                      {({ tokens, getTokenProps }) => (
                        <pre className="text-[13px] leading-[1.6] m-0 p-0 overflow-x-auto" style={{ background: "#0d1117" }}>
                          <code className="block">
                            {tokens.map((line, i) => (
                              <div key={i} className="flex hover:bg-[#161b22]" style={{ minHeight: "1.6em" }}>
                                <span className="shrink-0 w-12 text-right pr-4 select-none text-[#484f58] text-xs leading-[1.6]" style={{ paddingTop: "0.1em" }}>
                                  {i + 1}
                                </span>
                                <span className="flex-1 pr-4 whitespace-pre">
                                  {line.map((token, key) => (
                                    <span key={key} {...getTokenProps({ token })} />
                                  ))}
                                </span>
                              </div>
                            ))}
                          </code>
                        </pre>
                      )}
                    </Highlight>
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
