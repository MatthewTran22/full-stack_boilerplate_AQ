"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Highlight, themes } from "prism-react-renderer";
import { motion, AnimatePresence } from "framer-motion";
import { startClone, getClones, getPreviewUrl, resolveApiUrl, type CloneHistoryItem, type CloneFile, type CloneEvent, type CloneUsage } from "@/lib/api";
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
  Wrench,
  FileCode2,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
  Folder,
  Plus,
} from "lucide-react";

// ── Brand Logo (SVG) ──
function ClonrLogo({ size = 48, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 173 173" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
      <rect width="172.339" height="172.339" rx="10" fill="currentColor" className="text-white" />
      <rect x="79" y="36" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="72" y="49" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="65" y="63" width="33" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="59" y="76" width="19" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="51" y="90" width="60" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="45" y="104" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="110" y="104" width="18" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="40" y="118" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="115" y="118" width="21" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="23" y="131" width="45" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="109" y="131" width="40" height="7.5" rx="0.96" fill="hsl(var(--background))" />
    </svg>
  );
}

function ClonrLogoSmall({ className = "" }: { className?: string }) {
  return (
    <svg width="24" height="24" viewBox="0 0 173 173" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
      <rect width="172.339" height="172.339" rx="10" fill="currentColor" className="text-white" />
      <rect x="79" y="36" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="72" y="49" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="65" y="63" width="33" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="59" y="76" width="19" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="51" y="90" width="60" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="45" y="104" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="110" y="104" width="18" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="40" y="118" width="20" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="115" y="118" width="21" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="23" y="131" width="45" height="7.5" rx="0.96" fill="hsl(var(--background))" />
      <rect x="109" y="131" width="40" height="7.5" rx="0.96" fill="hsl(var(--background))" />
    </svg>
  );
}

// ── Framer motion presets ──
const stagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.08, delayChildren: 0.1 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 20, filter: "blur(8px)" },
  visible: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.6, ease: [0.16, 1, 0.3, 1] as const } },
};

const fadeIn = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.5 } },
};

type CloneStatus = "idle" | "scraping" | "generating" | "deploying" | "done" | "error";

type LogEntry = MessageLogEntry | FileLogEntry | ScreenshotLogEntry;

interface MessageLogEntry {
  kind: "message";
  id: number;
  icon: "search" | "camera" | "cpu" | "rocket" | "wrench" | "done" | "error";
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
  wrench: Wrench,
  done: CheckCircle2,
  error: AlertCircle,
};

function getIconForStatus(status: string, message: string): MessageLogEntry["icon"] {
  if (status === "error") return "error";
  if (status === "done") return "done";
  if (status === "fixing") return "wrench";
  if (status === "deploying") return "rocket";
  if (status === "generating" || status === "section_complete") return "cpu";
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
  const [aiUsage, setAiUsage] = useState<CloneUsage | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [inputFocused, setInputFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logIdRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const spotlightRef = useRef<HTMLDivElement>(null);

  // ── Mouse spotlight tracker ──
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = spotlightRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    el.style.setProperty("--mouse-x", `${x}%`);
    el.style.setProperty("--mouse-y", `${y}%`);
  }, []);

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
    if (!/^https?:\/\//i.test(targetUrl)) targetUrl = "https://" + targetUrl;

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
          if (data.file && data.lines) addFileLog(data.file, data.lines);
        } else if (data.status === "screenshot") {
          if (data.screenshot) {
            const id = ++logIdRef.current;
            setLogEntries((prev) => {
              const updated = prev.map((e) => e.kind === "message" && e.status === "active" ? { ...e, status: "done" as const } : e);
              return [...updated, { kind: "screenshot" as const, id, src: `data:image/png;base64,${data.screenshot}`, timestamp: new Date() }];
            });
          }
          addMessageLog("camera", "Screenshot captured", "done");
        } else if (data.status === "file_upload") {
          addMessageLog("rocket", data.message || "Uploading file...");
        } else if (data.status === "sandbox_ready") {
          if (data.preview_url) setPreviewUrl(resolveApiUrl(data.preview_url));
          addMessageLog("rocket", "Sandbox ready — loading preview...", "done");
        } else if (data.status === "scraping") {
          setStatus("scraping");
          addMessageLog(getIconForStatus(data.status, data.message || ""), data.message || "");
        } else if (data.status === "generating") {
          setStatus("generating");
          addMessageLog(getIconForStatus(data.status, data.message || ""), data.message || "");
        } else if (data.status === "section_complete") {
          const names = (data.components || []).join(", ");
          const msg = data.message || `Section complete${names ? ` (${names})` : ""}`;
          addMessageLog("cpu", msg, "done");
        } else if (data.status === "fixing") {
          setStatus("deploying");
          addMessageLog("wrench", data.message || "Fixing error...");
        } else if (data.status === "deploying") {
          setStatus("deploying");
          addMessageLog("rocket", data.message || "Deploying...");
        } else if (data.status === "done") {
          setStatus("done");
          addMessageLog("done", "Clone complete!", "done");
          if (data.usage) setAiUsage(data.usage);
          if (data.files && data.files.length > 0) { setGeneratedFiles(data.files); setActiveFile(data.files[0].path); }
          if (data.scaffold_paths) {
            setScaffoldPaths(data.scaffold_paths);
            const genPaths = (data.files || []).map((f: CloneFile) => f.path);
            const folders = new Set<string>();
            for (const p of genPaths) { const parts = p.split("/"); for (let i = 1; i < parts.length; i++) folders.add(parts.slice(0, i).join("/")); }
            setExpandedFolders(folders);
          }
          if (data.preview_url) setPreviewUrl(resolveApiUrl(data.preview_url));
          getClones(1, 30).then((res) => { setHistory(res.items); setClonePages(res.pages); setCloneTotal(res.total); setClonePage(1); }).catch(() => {});
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
    const previewLink = item.preview_url?.includes("/api/static/") ? resolveApiUrl(item.preview_url) : getPreviewUrl(item.id);
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
    setAiUsage(null);
  };

  const timeAgo = (dateStr: string) => {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  };

  const isLoading = status === "scraping" || status === "generating" || status === "deploying";
  const hasResult = status === "done" && (previewUrl || generatedFiles.length > 0);
  const showWorkspace = isLoading || hasResult || status === "error";
  const currentFileContent = generatedFiles.find((f) => f.path === activeFile)?.content || "";

  // ── Build nested file tree ──
  type TreeNode = { name: string; path: string; type: "file" | "folder"; generated: boolean; children: TreeNode[] };

  const fileTree = (() => {
    const root: TreeNode = { name: "", path: "", type: "folder", generated: false, children: [] };

    function ensureFolder(parts: string[]): TreeNode {
      let current = root;
      let currentPath = "";
      for (const part of parts) {
        currentPath = currentPath ? `${currentPath}/${part}` : part;
        let child = current.children.find((c) => c.name === part && c.type === "folder");
        if (!child) { child = { name: part, path: currentPath, type: "folder", generated: false, children: [] }; current.children.push(child); }
        current = child;
      }
      return current;
    }

    function addFile(filePath: string, generated: boolean) {
      const parts = filePath.split("/");
      const fileName = parts.pop()!;
      const folder = ensureFolder(parts);
      if (!folder.children.find((c) => c.name === fileName && c.type === "file"))
        folder.children.push({ name: fileName, path: filePath, type: "file", generated, children: [] });
    }

    for (const p of scaffoldPaths) addFile(p, false);
    for (const f of generatedFiles) addFile(f.path, true);

    function sortTree(nodes: TreeNode[]) {
      nodes.sort((a, b) => { if (a.type !== b.type) return a.type === "folder" ? -1 : 1; return a.name.localeCompare(b.name); });
      for (const n of nodes) if (n.children.length) sortTree(n.children);
    }
    sortTree(root.children);
    return root.children;
  })();

  const toggleFolder = (path: string) => {
    setExpandedFolders((prev) => { const next = new Set(prev); next.has(path) ? next.delete(path) : next.add(path); return next; });
  };

  const renderTreeNode = (node: TreeNode, depth: number): React.ReactNode => {
    if (node.type === "folder") {
      const isOpen = expandedFolders.has(node.path);
      return (
        <div key={node.path}>
          <button onClick={() => toggleFolder(node.path)} className="w-full flex items-center gap-1.5 py-1 text-left text-xs text-[hsl(0,0%,62%)] hover:text-[hsl(0,0%,86%)] hover:bg-white/[0.05] transition-colors" style={{ paddingLeft: depth * 14 + 8 }}>
            <ChevronRight className={`w-3 h-3 shrink-0 transition-transform duration-150 ${isOpen ? "rotate-90" : ""}`} />
            <Folder className="w-3.5 h-3.5 shrink-0 text-primary/50" />
            <span className="truncate font-mono">{node.name}</span>
          </button>
          {isOpen && node.children.map((child) => renderTreeNode(child, depth + 1))}
        </div>
      );
    }
    return (
      <button key={node.path} onClick={() => node.generated && setActiveFile(node.path)}
        className={`w-full flex items-center gap-1.5 py-1 text-left text-xs transition-colors ${
          activeFile === node.path ? "bg-primary/10 text-primary border-l-2 border-primary"
            : node.generated ? "text-[hsl(0,0%,62%)] hover:text-[hsl(0,0%,86%)] hover:bg-white/[0.05] cursor-pointer" : "text-[hsl(0,0%,35%)] cursor-default"
        }`} style={{ paddingLeft: depth * 14 + 22 }}>
        <FileCode2 className="w-3 h-3 shrink-0" />
        <span className="truncate font-mono">{node.name}</span>
        {node.generated && <span className="ml-auto text-[9px] font-mono text-primary/40 shrink-0 pr-2 uppercase tracking-wider">gen</span>}
      </button>
    );
  };

  // ══════════════════════════════════════
  // ── IDLE: Landing page with effects ──
  // ══════════════════════════════════════
  if (!showWorkspace) {
    return (
      <div
        ref={spotlightRef}
        onMouseMove={handleMouseMove}
        className="spotlight-container grain min-h-screen flex flex-col relative overflow-hidden"
      >
        {/* ── Layer 1: Dot grid ── */}
        <div className="fixed inset-0 dot-grid pointer-events-none" />

        {/* ── Layer 2: Grid lines ── */}
        <div className="fixed inset-0 grid-lines pointer-events-none" />

        {/* ── Layer 3: Mouse-following spotlight ── */}
        <div className="fixed inset-0 spotlight pointer-events-none" />

        {/* ── Layer 4: Radial vignette ── */}
        <div className="fixed inset-0 pointer-events-none" style={{
          background: "radial-gradient(ellipse 80% 60% at 50% 40%, transparent 30%, hsl(0 0% 7%) 80%)",
        }} />

        {/* Top nav */}
        <motion.nav
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="relative z-10 flex items-center justify-between px-8 py-5"
        >
          <div className="flex items-center gap-3">
            <ClonrLogoSmall />
            <span className="text-sm font-semibold tracking-tight">Clonr</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500/80 animate-pulse" />
            operational
          </div>
        </motion.nav>

        {/* Hero */}
        <motion.div
          variants={stagger}
          initial="hidden"
          animate="visible"
          className={`relative z-10 flex-1 flex flex-col items-center px-6 ${history.length > 0 ? "pt-8" : "justify-center"}`}
        >
          <motion.div variants={fadeUp} className="mb-10 text-center">
            <div className="inline-block mb-8 logo-glow">
              <ClonrLogo size={72} />
            </div>

            <motion.h1
              variants={fadeUp}
              className="text-5xl sm:text-6xl font-black tracking-tight mb-4 leading-[1.05]"
            >
              Clone any website
            </motion.h1>
            <motion.p variants={fadeUp} className="text-muted-foreground text-lg max-w-md mx-auto leading-relaxed font-light">
              Paste a URL. Get an AI-generated replica deployed to a live sandbox.
            </motion.p>
          </motion.div>

          {/* URL Input with border beam */}
          <motion.form
            variants={fadeUp}
            onSubmit={(e) => { e.preventDefault(); handleClone(); }}
            className="w-full max-w-xl"
          >
            <div className={`border-beam ${inputFocused ? "border-beam-focus" : "input-glow-idle"}`}>
              <div className="relative flex items-center bg-[hsl(0,0%,10%)] rounded-xl px-4 py-1.5">
                <Globe className="w-4 h-4 text-muted-foreground shrink-0" />
                <input
                  ref={inputRef}
                  type="text"
                  placeholder="https://example.com"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onFocus={() => setInputFocused(true)}
                  onBlur={() => setInputFocused(false)}
                  className="flex-1 bg-transparent border-0 outline-none px-3 py-2.5 text-sm font-mono placeholder:text-[hsl(0,0%,35%)]"
                />
                <button
                  type="submit"
                  disabled={!url.trim()}
                  className="shrink-0 bg-white text-black rounded-lg px-4 py-2 text-xs font-semibold hover:bg-white/90 disabled:opacity-20 disabled:cursor-not-allowed transition-all duration-200 flex items-center gap-2 tracking-wide uppercase"
                >
                  Clone
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
            <motion.p variants={fadeIn} className="mt-3 text-center text-[11px] text-muted-foreground/50 font-mono">
              URL &rarr; screenshot &rarr; AI generation &rarr; live sandbox
            </motion.p>
          </motion.form>

          {/* History */}
          {history.length > 0 && (
            <motion.div variants={fadeUp} className="mt-16 w-full max-w-5xl">
              <div className="flex items-center justify-between mb-5">
                <p className="text-[11px] font-mono text-muted-foreground/60 uppercase tracking-[0.15em] flex items-center gap-2">
                  <Clock className="w-3 h-3" />
                  Recent clones
                  {cloneTotal > 0 && <span className="text-muted-foreground/30">{cloneTotal}</span>}
                </p>
              </div>
              <motion.div
                variants={stagger}
                initial="hidden"
                animate="visible"
                className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5"
              >
                {history.map((item) => {
                  const domain = item.url.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
                  return (
                    <motion.button
                      key={item.id}
                      variants={fadeUp}
                      onClick={() => handleHistoryClick(item)}
                      className="gradient-card group text-left"
                    >
                      <div className="relative z-10 p-4 bg-[hsl(0,0%,10%)] rounded-[inherit]">
                        <div className="flex items-center gap-2 mb-2.5">
                          <div className="w-6 h-6 rounded-md bg-white/[0.06] flex items-center justify-center shrink-0">
                            <Globe className="w-3 h-3 text-muted-foreground group-hover:text-primary transition-colors duration-300" />
                          </div>
                          <span className="text-xs font-medium truncate text-[hsl(0,0%,82%)] group-hover:text-white transition-colors duration-300">{domain}</span>
                        </div>
                        <span className="text-[10px] text-muted-foreground/40 font-mono">{timeAgo(item.created_at)}</span>
                      </div>
                    </motion.button>
                  );
                })}
              </motion.div>

              {clonePages > 1 && (
                <div className="flex items-center justify-center gap-1 mt-8">
                  <button onClick={() => setClonePage((p) => Math.max(1, p - 1))} disabled={clonePage <= 1}
                    className="p-2 rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-white/[0.08] disabled:opacity-20 disabled:cursor-not-allowed transition-colors">
                    <ChevronLeft className="w-3.5 h-3.5" />
                  </button>
                  {Array.from({ length: clonePages }, (_, i) => i + 1)
                    .filter((p) => p === 1 || p === clonePages || Math.abs(p - clonePage) <= 1)
                    .reduce<(number | "...")[]>((acc, p, idx, arr) => {
                      if (idx > 0 && p - (arr[idx - 1]) > 1) acc.push("...");
                      acc.push(p); return acc;
                    }, [])
                    .map((p, idx) =>
                      p === "..." ? (
                        <span key={`ellipsis-${idx}`} className="px-1.5 text-muted-foreground/30 text-xs font-mono">...</span>
                      ) : (
                        <button key={p} onClick={() => setClonePage(p)}
                          className={`min-w-[28px] h-7 rounded-md text-xs font-mono transition-all duration-200 ${clonePage === p ? "bg-white text-black font-semibold" : "text-muted-foreground hover:text-foreground hover:bg-white/[0.08]"}`}>
                          {p}
                        </button>
                      )
                    )}
                  <button onClick={() => setClonePage((p) => Math.min(clonePages, p + 1))} disabled={clonePage >= clonePages}
                    className="p-2 rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-white/[0.08] disabled:opacity-20 disabled:cursor-not-allowed transition-colors">
                    <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </motion.div>
          )}
        </motion.div>

        {/* Footer */}
        <motion.footer
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6, duration: 0.5 }}
          className="relative z-10 px-8 py-5 flex items-center justify-between text-[10px] font-mono text-muted-foreground/30"
        >
          <span>AI-powered website cloning</span>
          <span>v1.0</span>
        </motion.footer>
      </div>
    );
  }

  // ══════════════════════════════════════════════
  // ── WORKSPACE: clean, professional, no effects ──
  // ══════════════════════════════════════════════
  return (
    <main className="grain h-screen flex flex-col bg-background">
      {/* Top bar */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-2 border-b border-[hsl(0,0%,15%)] bg-[hsl(0,0%,8%)]">
        <button onClick={reset} className="flex items-center gap-2.5 text-sm font-semibold hover:opacity-80 transition-opacity mr-1 tracking-tight">
          <ClonrLogoSmall />
          <span>Clonr</span>
        </button>
        <div className="w-px h-4 bg-[hsl(0,0%,18%)]" />
        <span className="text-xs text-muted-foreground/60 truncate max-w-[300px] font-mono">{url.replace(/^https?:\/\//, "")}</span>
        <div className="flex-1" />

        {hasResult && (
          <>
            <div className="flex items-center bg-[hsl(0,0%,12%)] rounded-md p-0.5 border border-[hsl(0,0%,18%)]">
              <button onClick={() => setShowCode(false)}
                className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-all duration-200 ${!showCode ? "bg-white text-black shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                <Eye className="w-3 h-3" /> Preview
              </button>
              <button onClick={() => setShowCode(true)}
                className={`flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-all duration-200 ${showCode ? "bg-white text-black shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
                <Code2 className="w-3 h-3" /> Code
              </button>
            </div>

            {showCode && activeFile && (
              <button onClick={handleCopyCode} className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-white/[0.08] transition-all font-mono">
                {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                {copied ? "copied" : "copy"}
              </button>
            )}

            {previewUrl && (
              <a href={previewUrl} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-white/[0.08] transition-all">
                <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </>
        )}

        <button onClick={reset} className="bg-white text-black rounded-md px-3.5 py-1.5 text-xs font-semibold hover:bg-white/90 transition-all flex items-center gap-1.5 tracking-wide uppercase">
          <Plus className="w-3 h-3" /> New
        </button>
      </div>

      {/* Main workspace */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left: Activity log */}
        {!sidebarOpen && (
          <button onClick={() => setSidebarOpen(true)} className="absolute left-2 top-3 z-20 p-1.5 rounded-md bg-[hsl(0,0%,12%)] border border-[hsl(0,0%,20%)] hover:bg-[hsl(0,0%,16%)] transition-colors" title="Show activity">
            <PanelLeftOpen className="w-3.5 h-3.5 text-muted-foreground" />
          </button>
        )}
        <div className={`shrink-0 border-r border-[hsl(0,0%,15%)] bg-[hsl(0,0%,8%)] flex flex-col z-10 transition-all duration-200 ${sidebarOpen ? "w-80" : "w-0 overflow-hidden border-r-0"}`}>
          <div className="px-4 py-3 border-b border-[hsl(0,0%,15%)] flex items-center justify-between">
            <h2 className="text-xs font-semibold flex items-center gap-2 uppercase tracking-[0.1em] text-muted-foreground">
              <Sparkles className="w-3.5 h-3.5 text-primary" /> Activity
            </h2>
            <button onClick={() => setSidebarOpen(false)} className="p-1 rounded-md hover:bg-white/[0.08] transition-colors" title="Hide activity">
              <PanelLeftClose className="w-3.5 h-3.5 text-muted-foreground/50" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-3 py-2.5">
            <div className="space-y-1.5">
              {(() => {
                type Phase = { id: string; label: string; icon: MessageLogEntry["icon"]; status: "active" | "done" | "error"; entries: LogEntry[]; summary?: string; startTime?: Date; endTime?: Date; };
                const phases: Phase[] = [];
                let current: Phase | null = null;
                const phaseMap: Record<string, string> = { search: "scrape", camera: "scrape", cpu: "generate", rocket: "deploy", wrench: "fix", done: "done", error: "error" };

                for (const entry of logEntries) {
                  let phaseKey = "other";
                  if (entry.kind === "message") phaseKey = phaseMap[entry.icon] || "other";
                  else if (entry.kind === "file") phaseKey = "generate";
                  else if (entry.kind === "screenshot") phaseKey = "scrape";

                  if (!current || current.id !== phaseKey) {
                    current = {
                      id: phaseKey,
                      label: phaseKey === "scrape" ? "Scraping website" : phaseKey === "generate" ? "Generating clone" : phaseKey === "deploy" ? "Deploying to sandbox" : phaseKey === "fix" ? "Auto-fixing errors" : phaseKey === "done" ? "Clone complete" : phaseKey === "error" ? "Error" : "Processing",
                      icon: phaseKey === "scrape" ? "search" : phaseKey === "generate" ? "cpu" : phaseKey === "deploy" ? "rocket" : phaseKey === "fix" ? "wrench" : phaseKey === "done" ? "done" : "error",
                      status: "active", entries: [], startTime: entry.timestamp,
                    };
                    phases.push(current);
                  }
                  current.entries.push(entry);
                  current.endTime = entry.timestamp;
                  if (entry.kind === "message") { if (entry.status === "done" || entry.status === "error") current.status = entry.status; }
                }

                for (const phase of phases) {
                  const files = phase.entries.filter((e): e is FileLogEntry => e.kind === "file");
                  const screenshots = phase.entries.filter((e): e is ScreenshotLogEntry => e.kind === "screenshot");
                  const parts: string[] = [];
                  if (screenshots.length) parts.push(`${screenshots.length} screenshot${screenshots.length > 1 ? "s" : ""}`);
                  if (files.length) { const totalLines = files.reduce((s, f) => s + f.lines, 0); parts.push(`${files.length} files (+${totalLines} lines)`); }
                  if (parts.length) phase.summary = parts.join(", ");
                }

                const togglePhase = (id: string) => { setExpandedPhases((prev) => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; }); };

                return phases.map((phase, idx) => {
                  const isOpen = expandedPhases.has(`${phase.id}-${idx}`);
                  const phaseKey = `${phase.id}-${idx}`;
                  const Icon = ICON_MAP[phase.icon];
                  const phaseDuration = phase.startTime && phase.endTime ? Math.round((phase.endTime.getTime() - phase.startTime.getTime()) / 1000) : 0;
                  const isActive = phase.status === "active";
                  const isDone = phase.id === "done";
                  const isError = phase.status === "error";

                  if (isDone) {
                    return (
                      <div key={phaseKey} className="rounded-lg bg-emerald-500/[0.06] border border-emerald-500/20">
                        <div className="flex items-center gap-2.5 py-2.5 px-3">
                          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                          <span className="text-xs font-medium text-emerald-400 flex-1">Clone complete</span>
                          {elapsed > 0 && <span className="text-[10px] text-muted-foreground/40 tabular-nums font-mono">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</span>}
                        </div>
                        {aiUsage && (
                          <div className="px-3 pb-2.5 flex items-center gap-3 text-[10px] font-mono text-muted-foreground/50">
                            <span>${aiUsage.total_cost.toFixed(4)}</span>
                            <span className="text-muted-foreground/20">|</span>
                            <span>{(aiUsage.tokens_in / 1000).toFixed(1)}k in</span>
                            <span className="text-muted-foreground/20">|</span>
                            <span>{(aiUsage.tokens_out / 1000).toFixed(1)}k out</span>
                            <span className="text-muted-foreground/20">|</span>
                            <span>{aiUsage.api_calls} call{aiUsage.api_calls !== 1 ? "s" : ""}</span>
                          </div>
                        )}
                      </div>
                    );
                  }

                  const files = phase.entries.filter((e): e is FileLogEntry => e.kind === "file");
                  const screenshots = phase.entries.filter((e): e is ScreenshotLogEntry => e.kind === "screenshot");

                  return (
                    <div key={phaseKey} className={`rounded-lg border transition-colors ${isActive ? "bg-primary/[0.04] border-primary/20" : isError ? "bg-destructive/[0.04] border-destructive/20" : "bg-white/[0.04] border-[hsl(0,0%,18%)]"}`}>
                      <button onClick={() => togglePhase(phaseKey)} className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left">
                        <ChevronRight className={`w-2.5 h-2.5 shrink-0 text-muted-foreground/40 transition-transform duration-150 ${isOpen ? "rotate-90" : ""}`} />
                        <div className={`shrink-0 ${isActive ? "text-primary" : isError ? "text-destructive" : "text-muted-foreground/40"}`}>
                          {isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Icon className="w-3.5 h-3.5" />}
                        </div>
                        <span className={`text-xs font-medium flex-1 min-w-0 truncate ${isActive ? "text-foreground" : isError ? "text-destructive" : "text-muted-foreground/70"}`}>{phase.label}</span>
                        {phase.summary && !isActive && <span className="text-[9px] text-muted-foreground/30 shrink-0 font-mono">{phase.summary}</span>}
                        {!isActive && !isError && <CheckCircle2 className="w-3 h-3 text-emerald-500/60 shrink-0" />}
                        {phaseDuration > 0 && !isActive && <span className="text-[9px] text-muted-foreground/25 shrink-0 tabular-nums font-mono">{phaseDuration}s</span>}
                      </button>

                      {isOpen && (
                        <div className="px-3 pb-2.5 space-y-0.5">
                          <div className="border-t border-[hsl(0,0%,15%)] pt-2">
                            {phase.entries.filter((e): e is MessageLogEntry => e.kind === "message").map((entry) => (
                              <div key={entry.id} className="flex items-center gap-2 py-0.5 px-1">
                                <span className="w-1 h-1 rounded-full bg-muted-foreground/20 shrink-0" />
                                <span className="text-[10px] text-muted-foreground/50 truncate flex-1 font-mono">{entry.message}</span>
                                <span className="text-[9px] text-muted-foreground/20 shrink-0 tabular-nums font-mono">{entry.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                              </div>
                            ))}
                            {screenshots.map((s) => (
                              <div key={s.id} className="flex items-center gap-2 py-0.5 px-1">
                                <Camera className="w-3 h-3 text-muted-foreground/30 shrink-0" />
                                <span className="text-[10px] text-muted-foreground/50 flex-1 font-mono">Screenshot captured</span>
                                <img src={s.src} alt="Screenshot" className="w-12 h-8 rounded border border-[hsl(0,0%,18%)] cursor-pointer hover:opacity-70 transition-opacity object-cover shrink-0" onClick={() => setExpandedScreenshot(s.src)} />
                              </div>
                            ))}
                            {files.length > 0 && (
                              <div className="mt-1.5 rounded-md bg-[hsl(0,0%,10%)] border border-[hsl(0,0%,15%)] overflow-hidden">
                                {files.map((f) => (
                                  <div key={f.id} className="flex items-center gap-2 px-2.5 py-1">
                                    <FileCode2 className="w-2.5 h-2.5 text-muted-foreground/30 shrink-0" />
                                    <span className="text-[10px] text-[hsl(0,0%,62%)] truncate flex-1 font-mono">{f.file}</span>
                                    <span className="text-[9px] font-mono text-emerald-400/50 shrink-0">+{f.lines}</span>
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
              <div className="mt-5 pt-4 border-t border-[hsl(0,0%,13%)]">
                <div className="flex items-center gap-2.5 text-xs text-muted-foreground/50 font-mono">
                  <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                  Working... {elapsed > 0 && `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Right: Preview / Code panel */}
        <div className="flex-1 flex flex-col overflow-hidden bg-[hsl(0,0%,7%)]">
          {isLoading && !previewUrl && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <div className="relative inline-flex items-center justify-center mb-8">
                  <div className="absolute w-24 h-24 rounded-full bg-primary/10 animate-pulse-ring" />
                  <div className="absolute w-16 h-16 rounded-full bg-primary/[0.06] animate-pulse" />
                  <ClonrLogo size={40} className="relative" />
                </div>
                <p className="text-muted-foreground/50 text-xs font-mono tracking-wide">building your clone...</p>
              </div>
            </div>
          )}

          {isLoading && previewUrl && (
            <div className="flex-1 relative">
              <iframe src={previewUrl} className="absolute inset-0 w-full h-full border-0" title="Cloned website preview" sandbox="allow-scripts allow-same-origin" />
            </div>
          )}

          {status === "error" && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center max-w-sm">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-destructive/10 mb-5 border border-destructive/20">
                  <X className="w-6 h-6 text-destructive" />
                </div>
                <h2 className="text-lg font-semibold mb-2 tracking-tight">Clone failed</h2>
                <p className="text-xs text-muted-foreground mb-5 font-mono leading-relaxed">{error}</p>
                <button onClick={reset} className="bg-white/[0.08] border border-[hsl(0,0%,20%)] rounded-lg px-5 py-2 text-xs font-medium hover:bg-white/[0.1] transition-colors">Try again</button>
              </div>
            </div>
          )}

          {hasResult && !showCode && previewUrl && (
            <div className="flex-1 relative">
              <iframe src={previewUrl} className="absolute inset-0 w-full h-full border-0" title="Cloned website preview" sandbox="allow-scripts allow-same-origin" />
            </div>
          )}
          {hasResult && !showCode && !previewUrl && generatedFiles.length > 0 && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <FileCode2 className="w-8 h-8 text-muted-foreground/30 mx-auto mb-4" />
                <p className="text-muted-foreground/50 text-xs mb-3 font-mono">No sandbox preview available</p>
                <button onClick={() => setShowCode(true)} className="text-primary text-xs hover:underline font-mono">View generated code</button>
              </div>
            </div>
          )}

          {/* Code mode */}
          {hasResult && showCode && generatedFiles.length > 0 && (
            <div className="flex-1 flex overflow-hidden">
              <div className="w-56 shrink-0 border-r border-[hsl(0,0%,13%)] bg-[hsl(0,0%,8%)] overflow-y-auto">
                <div className="px-3 py-2.5 border-b border-[hsl(0,0%,13%)]">
                  <p className="text-[10px] font-mono text-muted-foreground/40 uppercase tracking-[0.15em]">Explorer</p>
                </div>
                <div className="py-1">
                  {fileTree.length > 0
                    ? fileTree.map((node) => renderTreeNode(node, 0))
                    : generatedFiles.map((file) => (
                        <button key={file.path} onClick={() => setActiveFile(file.path)}
                          className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors ${activeFile === file.path ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-white/[0.05]"}`}>
                          <FileCode2 className="w-3 h-3 shrink-0" />
                          <span className="truncate font-mono text-xs">{file.path}</span>
                        </button>
                      ))
                  }
                </div>
              </div>

              <div className="flex-1 overflow-auto bg-[hsl(0,0%,7%)]">
                {activeFile && (
                  <div>
                    <div className="sticky top-0 z-10 flex items-center gap-2 px-4 py-2 bg-[hsl(0,0%,9%)] border-b border-[hsl(0,0%,15%)]">
                      <FileCode2 className="w-3 h-3 text-muted-foreground/40" />
                      <span className="text-[11px] font-mono text-muted-foreground/50">{activeFile}</span>
                      <span className="text-[10px] font-mono text-emerald-400/40 ml-auto">{generatedFiles.find(f => f.path === activeFile)?.lines || 0} lines</span>
                    </div>
                    <Highlight theme={themes.nightOwl} code={currentFileContent} language="tsx">
                      {({ tokens, getTokenProps }) => (
                        <pre className="text-[13px] leading-[1.7] m-0 p-0 overflow-x-auto" style={{ background: "hsl(0,0%,7%)" }}>
                          <code className="block">
                            {tokens.map((line, i) => (
                              <div key={i} className="flex hover:bg-white/[0.04]" style={{ minHeight: "1.7em" }}>
                                <span className="shrink-0 w-12 text-right pr-4 select-none text-[hsl(0,0%,28%)] text-xs leading-[1.7] font-mono" style={{ paddingTop: "0.1em" }}>{i + 1}</span>
                                <span className="flex-1 pr-4 whitespace-pre">{line.map((token, key) => (<span key={key} {...getTokenProps({ token })} />))}</span>
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
      <AnimatePresence>
        {expandedScreenshot && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-md cursor-pointer"
            onClick={() => setExpandedScreenshot(null)}
          >
            <button onClick={() => setExpandedScreenshot(null)} className="absolute top-6 right-6 p-2 rounded-lg bg-white/[0.08] hover:bg-white/[0.12] transition-colors border border-white/10">
              <X className="w-4 h-4 text-white" />
            </button>
            <motion.img
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              transition={{ type: "spring", damping: 25, stiffness: 300 }}
              src={expandedScreenshot}
              alt="Screenshot expanded"
              className="max-w-[90vw] max-h-[90vh] rounded-lg shadow-2xl object-contain"
              onClick={(e) => e.stopPropagation()}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}
