"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Highlight, themes } from "prism-react-renderer";
import { motion, AnimatePresence } from "framer-motion";
import JSZip from "jszip";
import { startClone, getClones, getCloneFiles, getPreviewUrl, resolveApiUrl, endSandbox, getBeaconEndUrl, login, getAuthStatus, getStoredToken, clearToken, type CloneHistoryItem, type CloneFile, type CloneEvent, type CloneUsage } from "@/lib/api";
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
  Download,
  Lock,
  LogOut,
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

type CloneStatus = "idle" | "scraping" | "generating" | "deploying" | "loading" | "done" | "error";

type LogEntry = MessageLogEntry | FileLogEntry | ScreenshotLogEntry | SectionLogEntry | UploadLogEntry;

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

interface SectionLogEntry {
  kind: "section";
  id: number;
  section: number;
  total: number;
  components: string[];
  timestamp: Date;
}

interface UploadLogEntry {
  kind: "upload";
  id: number;
  file: string;
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
  // ── Auth state ──
  const [isAuthed, setIsAuthed] = useState(false);
  const [authChecking, setAuthChecking] = useState(true);
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [authLoading, setAuthLoading] = useState(false);
  const [dailyClonesUsed, setDailyClonesUsed] = useState(0);
  const [dailyCloneLimit, setDailyCloneLimit] = useState(10);

  const [url, setUrl] = useState("");
  const [cloneId, setCloneId] = useState<string | null>(null);
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
  const [sectionTotal, setSectionTotal] = useState(0);
  const [sectionsComplete, setSectionsComplete] = useState<Set<number>>(new Set());
  const [elapsed, setElapsed] = useState(0);
  const [inputFocused, setInputFocused] = useState(false);
  const [backgrounded, setBackgrounded] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const logIdRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const spotlightRef = useRef<HTMLDivElement>(null);
  const cloneIdRef = useRef<string | null>(null);

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

  // ── Check auth on mount ──
  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setAuthChecking(false);
      return;
    }
    getAuthStatus()
      .then((s) => {
        setIsAuthed(true);
        setDailyClonesUsed(s.daily_clones_used);
        setDailyCloneLimit(s.daily_clone_limit);
      })
      .catch(() => {
        clearToken();
      })
      .finally(() => setAuthChecking(false));
  }, []);

  const handleLogin = async () => {
    if (!password.trim()) return;
    setAuthLoading(true);
    setAuthError(null);
    try {
      const res = await login(password.trim());
      setIsAuthed(true);
      setDailyClonesUsed(res.daily_clones_used);
      setDailyCloneLimit(res.daily_clone_limit);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setAuthLoading(false);
    }
  };

  const handleLogout = () => {
    clearToken();
    setIsAuthed(false);
    setPassword("");
    setAuthError(null);
  };

  useEffect(() => {
    getClones(clonePage, 5).then((res) => {
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

  // Clean up sandbox when user closes tab
  useEffect(() => {
    if (!cloneId) return;
    const handleBeforeUnload = () => {
      navigator.sendBeacon(getBeaconEndUrl(cloneId));
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [cloneId]);

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
    // Don't allow starting a new clone while one is in progress
    if (isLoading) return;
    let targetUrl = url.trim();
    if (!/^https?:\/\//i.test(targetUrl)) targetUrl = "https://" + targetUrl;

    // Clean up previous sandbox before starting new clone
    if (cloneIdRef.current) endSandbox(cloneIdRef.current);

    setBackgrounded(false);
    setStatus("scraping");
    setCloneId(null);
    cloneIdRef.current = null;
    setError(null);
    setPreviewUrl(null);
    setGeneratedFiles([]);
    setShowCode(false);
    setActiveFile(null);
    setLogEntries([]);
    setElapsed(0);
    setSectionTotal(0);
    setSectionsComplete(new Set());
    setExpandedPhases(new Set());
    logIdRef.current = 0;
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }

    addMessageLog("search", "Starting clone process...");

    try {
      await startClone(targetUrl, (data: CloneEvent) => {
        // Capture sandbox clone_id from the first event only (later events have DB id)
        if (data.clone_id && !cloneIdRef.current) {
          cloneIdRef.current = data.clone_id;
          setCloneId(data.clone_id);
        }
        if (data.status === "file_write") {
          if (data.file && data.lines) addFileLog(data.file, data.lines);
        } else if (data.status === "screenshot") {
          const imgs: string[] = data.screenshots ?? (data.screenshot ? [data.screenshot] : []);
          if (imgs.length) {
            setLogEntries((prev) => {
              const updated = prev.map((e) => e.kind === "message" && e.status === "active" ? { ...e, status: "done" as const } : e);
              const newEntries = imgs.map((s) => ({
                kind: "screenshot" as const,
                id: ++logIdRef.current,
                src: `data:image/png;base64,${s}`,
                timestamp: new Date(),
              }));
              return [...updated, ...newEntries];
            });
          }
          addMessageLog("camera", `${imgs.length} screenshot${imgs.length !== 1 ? "s" : ""} captured`, "done");
        } else if (data.status === "file_upload") {
          const uploadFile = data.file || (data.message || "").replace(/^Uploaded\s+/, "");
          const uid = ++logIdRef.current;
          setLogEntries((prev) => [
            ...prev,
            { kind: "upload" as const, id: uid, file: uploadFile, timestamp: new Date() },
          ]);
        } else if (data.status === "sandbox_ready") {
          if (data.preview_url) setPreviewUrl(resolveApiUrl(data.preview_url));
          addMessageLog("rocket", "Sandbox ready — loading preview...", "done");
        } else if (data.status === "scraping") {
          setStatus("scraping");
          addMessageLog(getIconForStatus(data.status, data.message || ""), data.message || "");
        } else if (data.status === "generating") {
          setStatus("generating");
          // Detect agent/section count from messages like:
          // "Splitting into 3 parallel agents (5 screenshots)..."
          // "AI is generating the clone (3 sections)..."
          const agentMatch = (data.message || "").match(/(\d+)\s+parallel\s+agents?/);
          const sectionMatch = (data.message || "").match(/\((\d+)\s+sections?\)/);
          if (agentMatch) setSectionTotal(parseInt(agentMatch[1], 10));
          else if (sectionMatch) setSectionTotal(parseInt(sectionMatch[1], 10));
          // Also detect from agent_start events
          if (data.total_agents && data.total_agents > 0) setSectionTotal(data.total_agents);
          addMessageLog(getIconForStatus(data.status, data.message || ""), data.message || "");
        } else if (data.status === "section_complete") {
          const sec = data.section || 0;
          const tot = data.total || 0;
          if (tot > 0) setSectionTotal(tot);
          setSectionsComplete((prev) => new Set(prev).add(sec));
          const id = ++logIdRef.current;
          setLogEntries((prev) => [
            ...prev,
            { kind: "section" as const, id, section: sec, total: tot, components: data.components || [], timestamp: new Date() },
          ]);
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
          getClones(1, 5).then((res) => { setHistory(res.items); setClonePages(res.pages); setCloneTotal(res.total); setClonePage(1); }).catch(() => {});
        } else if (data.status === "error") {
          setStatus("error");
          setError(data.message || "Unknown error");
          addMessageLog("error", data.message || "Unknown error", "error");
        }
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Something went wrong";
      if (msg.includes("Not authenticated") || msg.includes("password")) {
        clearToken();
        setIsAuthed(false);
        return;
      }
      setStatus("error");
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

  const handleExportZip = async () => {
    if (!generatedFiles.length) return;
    const zip = new JSZip();
    for (const file of generatedFiles) {
      zip.file(file.path, file.content);
    }
    const blob = await zip.generateAsync({ type: "blob" });
    const domain = url.replace(/^https?:\/\//, "").replace(/[/\\?#:]/g, "_").replace(/_+$/, "") || "clone";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${domain}-clone.zip`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const handleHistoryClick = async (item: CloneHistoryItem) => {
    if (cloneIdRef.current) endSandbox(cloneIdRef.current);
    setStatus("loading");
    setUrl(item.url);
    setCloneId(item.id);
    cloneIdRef.current = item.id;
    setPreviewUrl(null);
    setGeneratedFiles([]);
    setLogEntries([]);
    // Fetch files and preview in parallel
    const previewLink = item.preview_url?.includes("/api/static/") ? resolveApiUrl(item.preview_url) : getPreviewUrl(item.id);
    const files = await getCloneFiles(item.id);
    setGeneratedFiles(files);
    if (files.length > 0) setActiveFile(files[0].path);
    setPreviewUrl(previewLink);
    setLogEntries([{ kind: "message", id: 1, icon: "done", message: "Loaded from history", timestamp: new Date(), status: "done" }]);
    setStatus("done");
  };

  const goHome = () => {
    const active = status === "scraping" || status === "generating" || status === "deploying";
    if (active) {
      setBackgrounded(true);
    } else {
      reset();
    }
  };

  const reset = () => {
    if (cloneIdRef.current) endSandbox(cloneIdRef.current);
    setBackgrounded(false);
    setStatus("idle");
    setUrl("");
    setCloneId(null);
    cloneIdRef.current = null;
    setPreviewUrl(null);
    setGeneratedFiles([]);
    setError(null);
    setShowCode(false);
    setActiveFile(null);
    setLogEntries([]);
    setAiUsage(null);
    setSectionTotal(0);
    setSectionsComplete(new Set());
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

  const isLoading = status === "scraping" || status === "generating" || status === "deploying" || status === "loading";
  const hasResult = status === "done" && (previewUrl || generatedFiles.length > 0);
  const showWorkspace = !backgrounded && (isLoading || hasResult || status === "error");
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
  // ── AUTH: Loading / Password gate ──
  // ══════════════════════════════════════
  if (authChecking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!isAuthed) {
    return (
      <div className="grain min-h-screen flex flex-col items-center justify-center relative overflow-hidden">
        <div className="fixed inset-0 dot-grid pointer-events-none" />
        <div className="fixed inset-0 pointer-events-none" style={{
          background: "radial-gradient(ellipse 80% 60% at 50% 40%, transparent 30%, hsl(0 0% 7%) 80%)",
        }} />

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="relative z-10 flex flex-col items-center w-full max-w-sm px-6"
        >
          <div className="mb-8">
            <ClonrLogo size={64} />
          </div>

          <h1 className="text-2xl font-bold tracking-tight mb-2">Clonr</h1>
          <p className="text-muted-foreground text-sm mb-8">Enter the password to continue</p>

          <form
            onSubmit={(e) => { e.preventDefault(); handleLogin(); }}
            className="w-full space-y-4"
          >
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input
                type="password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
                className="w-full bg-[hsl(0,0%,10%)] border border-[hsl(0,0%,18%)] rounded-lg pl-10 pr-4 py-3 text-sm font-mono outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/25 transition-all placeholder:text-[hsl(0,0%,35%)]"
              />
            </div>

            {authError && (
              <motion.p
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-destructive text-xs font-mono text-center"
              >
                {authError}
              </motion.p>
            )}

            <button
              type="submit"
              disabled={!password.trim() || authLoading}
              className="w-full bg-white text-black rounded-lg py-3 text-sm font-semibold hover:bg-white/90 disabled:opacity-30 disabled:cursor-not-allowed transition-all flex items-center justify-center gap-2"
            >
              {authLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
              {authLoading ? "Checking..." : "Enter"}
            </button>
          </form>
        </motion.div>
      </div>
    );
  }

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
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500/80 animate-pulse" />
              operational
            </div>
            <button onClick={handleLogout} className="p-1.5 rounded-md hover:bg-white/[0.08] transition-colors text-muted-foreground/40 hover:text-muted-foreground" title="Sign out">
              <LogOut className="w-3.5 h-3.5" />
            </button>
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
            <div
              className={`inline-block mb-8 ${backgrounded ? "cursor-pointer" : "logo-glow"}`}
              onClick={backgrounded ? () => setBackgrounded(false) : undefined}
            >
              <div
                className={`relative ${backgrounded ? "logo-pulse-stage" : ""}`}
                style={backgrounded ? {
                  '--pulse-h': status === "scraping" ? 145 : status === "generating" ? 45 : status === "deploying" ? 0 : status === "done" ? 145 : 0,
                  '--pulse-s': status === "scraping" ? 80 : status === "generating" ? 100 : status === "deploying" ? 85 : status === "done" ? 80 : 85,
                } as React.CSSProperties : undefined}
              >
                <ClonrLogo size={72} />
                {backgrounded && (
                  <div className="absolute -bottom-7 left-1/2 -translate-x-1/2 whitespace-nowrap text-center">
                    {isLoading && (
                      <span className={`text-[10px] font-mono tracking-wide ${status === "scraping" ? "text-emerald-400/70" : status === "deploying" ? "text-red-400/70" : "text-amber-400/70"}`}>
                        {status === "scraping" ? "scraping" : status === "generating" ? "generating" : "deploying"}...
                      </span>
                    )}
                    {status === "done" && (
                      <span className="text-[10px] font-mono tracking-wide text-emerald-400/80">
                        done — click to view
                      </span>
                    )}
                    {status === "error" && (
                      <span className="text-[10px] font-mono tracking-wide text-red-400/80">
                        failed — click to view
                      </span>
                    )}
                  </div>
                )}
              </div>
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
                  disabled={!url.trim() || (backgrounded && isLoading)}
                  className="shrink-0 bg-white text-black rounded-lg px-4 py-2 text-xs font-semibold hover:bg-white/90 disabled:opacity-20 disabled:cursor-not-allowed transition-all duration-200 flex items-center gap-2 tracking-wide uppercase"
                >
                  {backgrounded && isLoading ? "Running..." : "Clone"}
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
            <motion.p variants={fadeIn} className="mt-3 text-center text-[11px] text-muted-foreground/50 font-mono">
              URL &rarr; screenshot &rarr; AI generation &rarr; live sandbox
            </motion.p>
          </motion.form>

          {/* History — list style like v0/bolt */}
          {history.length > 0 && (
            <motion.div variants={fadeUp} className="mt-14 w-full max-w-2xl">
              <div className="flex items-center justify-between mb-3 px-1">
                <p className="text-[11px] font-mono text-muted-foreground/50 uppercase tracking-[0.15em] flex items-center gap-2">
                  <Clock className="w-3 h-3" />
                  Recent clones
                  {cloneTotal > 0 && <span className="text-muted-foreground/25">{cloneTotal}</span>}
                </p>
              </div>
              <div className="rounded-xl border border-[hsl(0,0%,14%)] bg-[hsl(0,0%,9%)]/80 backdrop-blur-sm overflow-hidden divide-y divide-[hsl(0,0%,13%)]">
                {history.map((item, i) => {
                  const domain = item.url.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
                  const path = item.url.replace(/^https?:\/\//, "").replace(/^[^/]*/, "").replace(/\/$/, "") || "/";
                  return (
                    <motion.button
                      key={item.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.3, delay: i * 0.03, ease: [0.16, 1, 0.3, 1] }}
                      onClick={() => handleHistoryClick(item)}
                      className="w-full flex items-center gap-3 px-4 py-3 text-left group hover:bg-white/[0.03] transition-colors duration-200"
                    >
                      {/* Favicon */}
                      <img
                        src={`https://www.google.com/s2/favicons?domain=${domain}&sz=32`}
                        alt=""
                        className="w-5 h-5 rounded shrink-0 bg-white/[0.06] group-hover:scale-110 transition-transform duration-200"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; (e.target as HTMLImageElement).nextElementSibling?.classList.remove("hidden"); }}
                      />
                      <div className="w-5 h-5 rounded bg-white/[0.06] items-center justify-center shrink-0 hidden">
                        <Globe className="w-3 h-3 text-muted-foreground/50" />
                      </div>

                      {/* Domain + path */}
                      <div className="flex-1 min-w-0 flex items-baseline gap-1.5">
                        <span className="text-sm font-medium text-[hsl(0,0%,85%)] group-hover:text-white transition-colors duration-200 truncate">{domain}</span>
                        {path !== "/" && (
                          <span className="text-xs text-muted-foreground/30 font-mono truncate hidden sm:inline">{path}</span>
                        )}
                      </div>

                      {/* Time */}
                      <span className="text-[11px] text-muted-foreground/30 font-mono shrink-0 tabular-nums">{timeAgo(item.created_at)}</span>

                      {/* Arrow */}
                      <ArrowRight className="w-3.5 h-3.5 text-muted-foreground/0 group-hover:text-muted-foreground/40 transition-all duration-200 shrink-0" />
                    </motion.button>
                  );
                })}
              </div>

              {clonePages > 1 && (
                <div className="flex items-center justify-center gap-1 mt-6">
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
        <button onClick={goHome} className="flex items-center gap-2.5 text-sm font-semibold hover:opacity-80 transition-opacity mr-1 tracking-tight">
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

            {generatedFiles.length > 0 && (
              <button onClick={handleExportZip} className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-white/[0.08] transition-all font-mono">
                <Download className="w-3 h-3" /> export
              </button>
            )}
          </>
        )}

        <button onClick={goHome} className="bg-white text-black rounded-md px-3.5 py-1.5 text-xs font-semibold hover:bg-white/90 transition-all flex items-center gap-1.5 tracking-wide uppercase">
          <Plus className="w-3 h-3" /> New
        </button>
      </div>

      {/* Main workspace */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left: Activity log */}
        {!sidebarOpen && status !== "loading" && (
          <button onClick={() => setSidebarOpen(true)} className="absolute left-2 top-3 z-20 p-1.5 rounded-md bg-[hsl(0,0%,12%)] border border-[hsl(0,0%,20%)] hover:bg-[hsl(0,0%,16%)] transition-colors" title="Show activity">
            <PanelLeftOpen className="w-3.5 h-3.5 text-muted-foreground" />
          </button>
        )}
        <div className={`shrink-0 border-r border-[hsl(0,0%,15%)] bg-[hsl(0,0%,8%)] flex flex-col z-10 transition-all duration-200 ${sidebarOpen && status !== "loading" ? "w-80" : "w-0 overflow-hidden border-r-0"}`}>
          <div className="px-4 py-3 border-b border-[hsl(0,0%,15%)] flex items-center justify-between">
            <h2 className="text-xs font-semibold flex items-center gap-2 uppercase tracking-[0.1em] text-muted-foreground">
              <Sparkles className="w-3.5 h-3.5 text-primary" /> Activity
            </h2>
            <button onClick={() => setSidebarOpen(false)} className="p-1 rounded-md hover:bg-white/[0.08] transition-colors" title="Hide activity">
              <PanelLeftClose className="w-3.5 h-3.5 text-muted-foreground/50" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-4">
            <div className="relative">
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
                  else if (entry.kind === "section") phaseKey = "generate";
                  else if (entry.kind === "upload") phaseKey = "deploy";

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
                  const sections = phase.entries.filter((e): e is SectionLogEntry => e.kind === "section");
                  const uploads = phase.entries.filter((e): e is UploadLogEntry => e.kind === "upload");
                  const parts: string[] = [];
                  if (screenshots.length) parts.push(`${screenshots.length} screenshot${screenshots.length > 1 ? "s" : ""}`);
                  if (sections.length > 0) parts.push(`${sections.length} agent${sections.length > 1 ? "s" : ""}`);
                  if (files.length) { const totalLines = files.reduce((s, f) => s + f.lines, 0); parts.push(`${files.length} files · ${totalLines} lines`); }
                  if (uploads.length > 0) parts.push(`${uploads.length} file${uploads.length > 1 ? "s" : ""} uploaded`);
                  if (parts.length) phase.summary = parts.join(" · ");
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
                  const isLast = idx === phases.length - 1;

                  const files = phase.entries.filter((e): e is FileLogEntry => e.kind === "file");
                  const screenshots = phase.entries.filter((e): e is ScreenshotLogEntry => e.kind === "screenshot");

                  // Timeline dot color
                  const dotColor = isDone ? "bg-emerald-400" : isError ? "bg-destructive" : isActive ? "bg-primary" : "bg-emerald-500/60";

                  return (
                    <div key={phaseKey} className="relative flex gap-3.5">
                      {/* Timeline spine */}
                      <div className="flex flex-col items-center shrink-0 pt-0.5">
                        <div className={`relative z-10 w-2 h-2 rounded-full ${dotColor} ${isActive ? "ring-[3px] ring-primary/20" : ""}`}>
                          {isActive && <div className="absolute inset-0 rounded-full bg-primary animate-ping opacity-40" />}
                        </div>
                        {!isLast && (
                          <div className="w-px flex-1 mt-1.5 bg-gradient-to-b from-[hsl(0,0%,22%)] to-[hsl(0,0%,12%)]" />
                        )}
                      </div>

                      {/* Phase content */}
                      <div className={`flex-1 min-w-0 ${!isLast ? "pb-5" : "pb-1"}`}>
                        {/* Phase header */}
                        <button onClick={() => !isDone && togglePhase(phaseKey)} className={`w-full flex items-center gap-2 text-left group ${isDone ? "cursor-default" : ""}`}>
                          <div className={`shrink-0 ${isActive ? "text-primary" : isDone ? "text-emerald-400" : isError ? "text-destructive" : "text-muted-foreground/50"}`}>
                            {isActive ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Icon className="w-3.5 h-3.5" />}
                          </div>
                          <span className={`text-[13px] font-medium flex-1 min-w-0 ${isActive ? "text-foreground" : isDone ? "text-emerald-400" : isError ? "text-destructive" : "text-muted-foreground/80"}`}>{phase.label}</span>
                          {phaseDuration > 0 && !isActive && <span className="text-[10px] text-muted-foreground/30 tabular-nums font-mono">{phaseDuration}s</span>}
                          {!isDone && !isActive && !isError && (
                            <ChevronRight className={`w-3 h-3 text-muted-foreground/20 transition-transform duration-150 group-hover:text-muted-foreground/40 ${isOpen ? "rotate-90" : ""}`} />
                          )}
                        </button>

                        {/* Phase summary line */}
                        {phase.summary && !isActive && !isDone && (
                          <p className="text-[10px] text-muted-foreground/40 font-mono mt-0.5 ml-[22px]">{phase.summary}</p>
                        )}

                        {/* Done: usage stats */}
                        {isDone && (
                          <div className="mt-2 ml-[22px]">
                            <div className="flex items-center gap-2 text-[10px] text-muted-foreground/40 font-mono">
                              {elapsed > 0 && <span className="tabular-nums">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")} total</span>}
                            </div>
                            {aiUsage && (
                              <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1">
                                <div className="flex items-baseline gap-1.5">
                                  <span className="text-[10px] text-muted-foreground/30 font-mono">cost</span>
                                  <span className="text-[11px] text-muted-foreground/60 font-mono tabular-nums">${aiUsage.total_cost.toFixed(4)}</span>
                                </div>
                                <div className="flex items-baseline gap-1.5">
                                  <span className="text-[10px] text-muted-foreground/30 font-mono">calls</span>
                                  <span className="text-[11px] text-muted-foreground/60 font-mono tabular-nums">{aiUsage.api_calls}</span>
                                </div>
                                <div className="flex items-baseline gap-1.5">
                                  <span className="text-[10px] text-muted-foreground/30 font-mono">in</span>
                                  <span className="text-[11px] text-muted-foreground/60 font-mono tabular-nums">{(aiUsage.tokens_in / 1000).toFixed(1)}k</span>
                                </div>
                                <div className="flex items-baseline gap-1.5">
                                  <span className="text-[10px] text-muted-foreground/30 font-mono">out</span>
                                  <span className="text-[11px] text-muted-foreground/60 font-mono tabular-nums">{(aiUsage.tokens_out / 1000).toFixed(1)}k</span>
                                </div>
                              </div>
                            )}
                          </div>
                        )}

                        {/* Active state: generic spinner for non-generate/non-deploy phases */}
                        {isActive && phase.id !== "generate" && phase.id !== "deploy" && phase.id !== "fix" && (
                          <div className="mt-1.5 ml-[22px] flex items-center gap-2">
                            <div className="w-1 h-1 rounded-full bg-primary/60 animate-pulse" />
                            <span className="text-[10px] text-muted-foreground/40 font-mono">
                              {elapsed > 0 && `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`}
                            </span>
                          </div>
                        )}

                        {/* ── Parallel agents view for generate phase ── */}
                        {phase.id === "generate" && sectionTotal > 0 && (
                          <div className="mt-2.5 ml-[22px] space-y-2">
                            {Array.from({ length: sectionTotal }, (_, i) => i + 1).map((sec) => {
                              const isDoneSec = sectionsComplete.has(sec);
                              const sectionEntry = phase.entries.find((e): e is SectionLogEntry => e.kind === "section" && e.section === sec);
                              const comps = sectionEntry?.components || [];

                              return (
                                <div key={sec} className="group">
                                  {/* Agent header */}
                                  <div className="flex items-center gap-2 mb-1">
                                    <div className={`w-4 h-4 rounded flex items-center justify-center text-[9px] font-mono font-bold ${isDoneSec ? "bg-emerald-500/15 text-emerald-400" : "bg-primary/15 text-primary"}`}>
                                      {sec}
                                    </div>
                                    <span className={`text-[11px] font-medium ${isDoneSec ? "text-muted-foreground/60" : "text-foreground/80"}`}>
                                      Agent {sec}
                                    </span>
                                    {isDoneSec && <CheckCircle2 className="w-3 h-3 text-emerald-500/50 ml-auto" />}
                                    {!isDoneSec && isActive && (
                                      <span className="text-[9px] text-primary/50 font-mono ml-auto">running</span>
                                    )}
                                  </div>

                                  {/* Progress bar */}
                                  <div className="h-1.5 rounded-full bg-white/[0.04] overflow-hidden">
                                    <div
                                      className={`h-full rounded-full transition-all duration-700 ease-out ${
                                        isDoneSec
                                          ? "w-full bg-gradient-to-r from-emerald-500/40 to-emerald-400/60"
                                          : "agent-bar-running bg-gradient-to-r from-primary/30 via-primary/50 to-primary/30"
                                      }`}
                                    />
                                  </div>

                                  {/* Components generated */}
                                  {isDoneSec && comps.length > 0 && (
                                    <div className="flex flex-wrap gap-1 mt-1.5">
                                      {comps.map((c) => (
                                        <span key={c} className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-white/[0.04] text-muted-foreground/50">
                                          {c}
                                        </span>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              );
                            })}

                            {/* Overall progress */}
                            {isActive && (
                              <div className="flex items-center gap-2 pt-1">
                                <span className="text-[10px] text-muted-foreground/35 font-mono">
                                  {sectionsComplete.size}/{sectionTotal} agents complete
                                </span>
                                {elapsed > 0 && (
                                  <span className="text-[10px] text-muted-foreground/25 font-mono ml-auto tabular-nums">
                                    {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        )}

                        {/* Active generate with NO sections (single-section mode) */}
                        {isActive && phase.id === "generate" && sectionTotal === 0 && (
                          <div className="mt-1.5 ml-[22px] flex items-center gap-2">
                            <div className="w-1 h-1 rounded-full bg-primary/60 animate-pulse" />
                            <span className="text-[10px] text-muted-foreground/40 font-mono">
                              {elapsed > 0 && `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`}
                            </span>
                          </div>
                        )}

                        {/* ── Deploy phase: step checklist with file uploads ── */}
                        {(phase.id === "deploy" || phase.id === "fix") && (() => {
                          const msgs = phase.entries.filter((e): e is MessageLogEntry => e.kind === "message");
                          const uploadEntries = phase.entries.filter((e): e is UploadLogEntry => e.kind === "upload");
                          const totalFilesToUpload = generatedFiles.length || uploadEntries.length;

                          // Derive deploy steps from messages
                          type DeployStep = { label: string; done: boolean; active: boolean; icon: "rocket" | "wrench" };
                          const steps: DeployStep[] = [];
                          const msgTexts = msgs.map((m) => m.message.toLowerCase());

                          const hasSandboxWait = msgTexts.some((m) => m.includes("waiting for sandbox") || m.includes("creating sandbox"));
                          const hasUpload = uploadEntries.length > 0 || msgTexts.some((m) => m.includes("uploading"));
                          const hasPreview = msgTexts.some((m) => m.includes("preview") || m.includes("dev server"));
                          const hasFix = msgs.some((m) => m.icon === "wrench") || phase.id === "fix";

                          if (hasSandboxWait || isActive) {
                            steps.push({
                              label: "Creating sandbox",
                              done: hasUpload || hasPreview,
                              active: !hasUpload && !hasPreview && isActive,
                              icon: "rocket",
                            });
                          }
                          if (hasUpload || isActive) {
                            steps.push({
                              label: `Uploading files${totalFilesToUpload > 0 ? ` (${uploadEntries.length}/${totalFilesToUpload})` : ""}`,
                              done: uploadEntries.length >= totalFilesToUpload && uploadEntries.length > 0,
                              active: uploadEntries.length > 0 && uploadEntries.length < totalFilesToUpload && isActive,
                              icon: "rocket",
                            });
                          }
                          if (hasFix) {
                            steps.push({
                              label: "Auto-fixing errors",
                              done: msgs.some((m) => m.status === "done" && m.icon === "wrench"),
                              active: msgs.some((m) => m.status === "active" && m.icon === "wrench"),
                              icon: "wrench",
                            });
                          }
                          if (hasPreview || (hasUpload && uploadEntries.length >= totalFilesToUpload)) {
                            steps.push({
                              label: "Starting preview",
                              done: msgTexts.some((m) => m.includes("sandbox ready")),
                              active: !msgTexts.some((m) => m.includes("sandbox ready")) && isActive,
                              icon: "rocket",
                            });
                          }

                          // If no steps detected yet but phase is active, show generic
                          if (steps.length === 0 && isActive) {
                            steps.push({ label: "Preparing deployment", done: false, active: true, icon: "rocket" });
                          }

                          return (
                            <div className="mt-2.5 ml-[22px] space-y-0.5">
                              {/* Step checklist */}
                              {steps.map((step, si) => (
                                <div key={si} className="flex items-center gap-2 py-1">
                                  {step.done ? (
                                    <CheckCircle2 className="w-3 h-3 text-emerald-500/60 shrink-0" />
                                  ) : step.active ? (
                                    <Loader2 className="w-3 h-3 text-primary animate-spin shrink-0" />
                                  ) : (
                                    <div className="w-3 h-3 rounded-full border border-[hsl(0,0%,20%)] shrink-0" />
                                  )}
                                  <span className={`text-[11px] font-mono ${step.done ? "text-muted-foreground/50" : step.active ? "text-foreground/80" : "text-muted-foreground/30"}`}>
                                    {step.label}
                                  </span>
                                </div>
                              ))}

                              {/* File upload progress bar */}
                              {uploadEntries.length > 0 && totalFilesToUpload > 0 && (
                                <div className="pt-1.5">
                                  <div className="h-1 rounded-full bg-white/[0.04] overflow-hidden">
                                    <div
                                      className="h-full rounded-full bg-gradient-to-r from-primary/40 to-primary/60 transition-all duration-500 ease-out"
                                      style={{ width: `${Math.min(100, (uploadEntries.length / totalFilesToUpload) * 100)}%` }}
                                    />
                                  </div>
                                  {/* Uploaded file names */}
                                  <div className="mt-1.5 space-y-px">
                                    {uploadEntries.slice(-5).map((u) => (
                                      <div key={u.id} className="flex items-center gap-1.5 py-0.5">
                                        <CheckCircle2 className="w-2 h-2 text-emerald-500/40 shrink-0" />
                                        <span className="text-[9px] font-mono text-muted-foreground/40 truncate">{u.file}</span>
                                      </div>
                                    ))}
                                    {uploadEntries.length > 5 && (
                                      <span className="text-[9px] font-mono text-muted-foreground/25 pl-3.5">+{uploadEntries.length - 5} more</span>
                                    )}
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })()}

                        {/* Expanded details */}
                        {(isOpen || isActive) && !isDone && (
                          <div className="mt-2 ml-[22px] space-y-1">
                            {phase.entries.filter((e): e is MessageLogEntry => e.kind === "message").map((entry) => (
                              <div key={entry.id} className="flex items-center gap-2 py-0.5">
                                <span className="w-0.5 h-0.5 rounded-full bg-muted-foreground/25 shrink-0" />
                                <span className="text-[10px] text-muted-foreground/45 flex-1 min-w-0 truncate font-mono">{entry.message}</span>
                              </div>
                            ))}
                            {screenshots.length > 0 && (
                              <div className="mt-1.5 flex gap-1.5 flex-wrap">
                                {screenshots.map((s) => (
                                  <img
                                    key={s.id}
                                    src={s.src} alt="Screenshot"
                                    className="w-[30%] max-w-[140px] rounded-md border border-[hsl(0,0%,15%)] cursor-pointer hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5 transition-all duration-200 object-cover"
                                    onClick={() => setExpandedScreenshot(s.src)}
                                  />
                                ))}
                              </div>
                            )}
                            {files.length > 0 && (
                              <div className="mt-1.5 space-y-px rounded-md overflow-hidden">
                                {files.map((f) => (
                                  <div key={f.id} className="flex items-center gap-2 py-1 px-2 bg-white/[0.02] first:rounded-t-md last:rounded-b-md">
                                    <FileCode2 className="w-2.5 h-2.5 text-muted-foreground/25 shrink-0" />
                                    <span className="text-[10px] text-muted-foreground/55 truncate flex-1 font-mono">{f.file}</span>
                                    <span className="text-[9px] font-mono text-emerald-400/50 shrink-0">+{f.lines}</span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                });
              })()}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>

        {/* Right: Preview / Code panel */}
        <div className="flex-1 flex flex-col overflow-hidden bg-[hsl(0,0%,7%)]">
          {isLoading && !previewUrl && (
            <div className="flex-1 flex items-center justify-center relative overflow-hidden">
              {/* PCB grid background */}
              <div className="absolute inset-0 pcb-grid" />
              {/* Radial glow from center — shifts with stage */}
              <div className="absolute inset-0 transition-all duration-1000" style={{
                background: status === "scraping"
                  ? "radial-gradient(circle at 50% 45%, hsla(145, 80%, 50%, 0.1) 0%, hsla(160, 90%, 45%, 0.04) 25%, transparent 60%)"
                  : status === "deploying"
                  ? "radial-gradient(circle at 50% 45%, hsla(0, 85%, 58%, 0.1) 0%, hsla(350, 80%, 55%, 0.04) 25%, transparent 60%)"
                  : status === "loading"
                  ? "radial-gradient(circle at 50% 45%, hsla(210, 85%, 55%, 0.1) 0%, hsla(220, 80%, 50%, 0.04) 25%, transparent 60%)"
                  : "radial-gradient(circle at 50% 45%, hsla(45, 100%, 55%, 0.1) 0%, hsla(38, 100%, 58%, 0.04) 25%, transparent 60%)",
              }} />
              {/* Animated aurora bands — color-coded by stage */}
              <div className="absolute inset-0 overflow-hidden">
                <div className={`aurora-band aurora-band-1 ${status === "scraping" ? "aurora-green" : status === "deploying" ? "aurora-red" : status === "loading" ? "aurora-blue" : "aurora-yellow"}`} />
                <div className={`aurora-band aurora-band-2 ${status === "scraping" ? "aurora-green" : status === "deploying" ? "aurora-red" : status === "loading" ? "aurora-blue" : "aurora-yellow"}`} />
                <div className={`aurora-band aurora-band-3 ${status === "scraping" ? "aurora-green" : status === "deploying" ? "aurora-red" : status === "loading" ? "aurora-blue" : "aurora-yellow"}`} />
              </div>
              {/* Vignette */}
              <div className="absolute inset-0" style={{
                background: "radial-gradient(ellipse 70% 60% at 50% 50%, transparent 30%, hsl(0 0% 7%) 80%)",
              }} />
              <div className="text-center relative z-10">
                <div className="relative inline-flex items-center justify-center mb-10" style={{ width: 280, height: 280 }}>
                  {/* Circuit traces SVG */}
                  <svg className="absolute inset-0 w-full h-full" viewBox="0 0 280 280" fill="none">
                    {/* Circuit traces — lines flowing inward toward the CPU */}
                    {(() => {
                      // Stage-based color palettes
                      const palettes = {
                        scraping:   ["hsl(145, 80%, 50%)", "hsl(160, 90%, 45%)", "hsl(130, 85%, 55%)", "hsl(170, 75%, 48%)", "hsl(140, 95%, 40%)"],
                        generating: ["hsl(45, 100%, 55%)", "hsl(38, 100%, 58%)", "hsl(50, 95%, 50%)", "hsl(32, 100%, 60%)", "hsl(55, 90%, 52%)"],
                        deploying:  ["hsl(0, 85%, 58%)", "hsl(350, 80%, 55%)", "hsl(10, 90%, 55%)", "hsl(340, 75%, 60%)", "hsl(15, 85%, 50%)"],
                        loading:    ["hsl(210, 85%, 55%)", "hsl(220, 80%, 50%)", "hsl(200, 90%, 50%)", "hsl(230, 75%, 55%)", "hsl(215, 85%, 48%)"],
                      };
                      const colors = palettes[status as keyof typeof palettes] || palettes.generating;
                      return [
                        // Top traces
                        { d: "M140 0 L140 40 L140 90", ci: 0, delay: 0 },
                        { d: "M100 5 L100 50 L120 70 L120 95", ci: 1, delay: 0.3 },
                        { d: "M180 5 L180 50 L160 70 L160 95", ci: 2, delay: 0.6 },
                        { d: "M60 15 L60 55 L95 90 L110 90 L110 100", ci: 3, delay: 0.9 },
                        { d: "M220 15 L220 55 L185 90 L170 90 L170 100", ci: 4, delay: 1.2 },
                        // Bottom traces
                        { d: "M140 280 L140 240 L140 190", ci: 0, delay: 0.4 },
                        { d: "M100 275 L100 230 L120 210 L120 185", ci: 1, delay: 0.7 },
                        { d: "M180 275 L180 230 L160 210 L160 185", ci: 2, delay: 1.0 },
                        { d: "M60 265 L60 225 L95 190 L110 190 L110 180", ci: 3, delay: 1.3 },
                        { d: "M220 265 L220 225 L185 190 L170 190 L170 180", ci: 4, delay: 0.2 },
                        // Left traces
                        { d: "M0 140 L40 140 L90 140", ci: 0, delay: 0.8 },
                        { d: "M5 100 L50 100 L70 120 L95 120", ci: 1, delay: 1.1 },
                        { d: "M5 180 L50 180 L70 160 L95 160", ci: 2, delay: 0.5 },
                        { d: "M15 60 L55 60 L90 95 L90 110 L100 110", ci: 3, delay: 1.4 },
                        { d: "M15 220 L55 220 L90 185 L90 170 L100 170", ci: 4, delay: 0.1 },
                        // Right traces
                        { d: "M280 140 L240 140 L190 140", ci: 0, delay: 1.2 },
                        { d: "M275 100 L230 100 L210 120 L185 120", ci: 1, delay: 0.6 },
                        { d: "M275 180 L230 180 L210 160 L185 160", ci: 2, delay: 0.9 },
                        { d: "M265 60 L225 60 L190 95 L190 110 L180 110", ci: 3, delay: 0.3 },
                        { d: "M265 220 L225 220 L190 185 L190 170 L180 170", ci: 4, delay: 1.5 },
                        // Diagonal corner traces
                        { d: "M30 30 L65 65 L90 90 L100 100", ci: 0, delay: 0.15 },
                        { d: "M250 30 L215 65 L190 90 L180 100", ci: 3, delay: 0.75 },
                        { d: "M30 250 L65 215 L90 190 L100 180", ci: 4, delay: 1.35 },
                        { d: "M250 250 L215 215 L190 190 L180 180", ci: 1, delay: 0.45 },
                      ].map((trace) => ({ ...trace, color: colors[trace.ci] }));
                    })().map((trace, i) => (
                      <g key={i}>
                        {/* Faint static trace path */}
                        <path d={trace.d} stroke="white" strokeOpacity={0.06} strokeWidth={1.5} fill="none" />
                        {/* Animated glowing pulse */}
                        <path
                          d={trace.d}
                          stroke={trace.color}
                          strokeWidth={2}
                          fill="none"
                          className="circuit-trace"
                          style={{
                            animation: `circuit-pulse 2.2s ease-in-out ${trace.delay}s infinite`,
                            filter: `drop-shadow(0 0 4px ${trace.color})`,
                          }}
                        />
                        {/* Bright tip dot */}
                        <circle r="2" fill={trace.color} opacity={0}>
                          <animateMotion
                            dur="2.2s"
                            begin={`${trace.delay}s`}
                            repeatCount="indefinite"
                            path={trace.d}
                          />
                          <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.15;0.85;1" dur="2.2s" begin={`${trace.delay}s`} repeatCount="indefinite" />
                        </circle>
                      </g>
                    ))}
                    {/* Junction dots at trace endpoints near CPU */}
                    {[
                      [140, 90], [120, 95], [160, 95], [110, 100], [170, 100],
                      [140, 190], [120, 185], [160, 185], [110, 180], [170, 180],
                      [90, 140], [95, 120], [95, 160], [100, 110], [100, 170],
                      [190, 140], [185, 120], [185, 160], [180, 110], [180, 170],
                      [100, 100], [180, 100], [100, 180], [180, 180],
                    ].map(([cx, cy], i) => (
                      <circle key={`dot-${i}`} cx={cx} cy={cy} r="2" fill="white" opacity={0.2} />
                    ))}
                  </svg>
                  {/* CPU core — the logo */}
                  <div className={`relative z-10 w-24 h-24 rounded-2xl bg-[hsl(0,0%,9%)] border border-white/10 flex items-center justify-center transition-shadow duration-1000 ${status === "scraping" ? "cpu-core-green" : status === "deploying" ? "cpu-core-red" : status === "loading" ? "cpu-core-blue" : "cpu-core-yellow"}`}>
                    <ClonrLogo size={56} className="relative" />
                  </div>
                </div>
                <p className="text-muted-foreground/50 text-xs font-mono tracking-wide">{status === "loading" ? "loading clone..." : "building your clone..."}</p>
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
