const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Auth helpers ──

export interface AuthResponse {
  token: string;
  daily_clones_used: number;
  daily_clone_limit: number;
}

export interface AuthStatus {
  daily_clones_used: number;
  daily_clone_limit: number;
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("clonr_token");
}

export function storeToken(token: string): void {
  localStorage.setItem("clonr_token", token);
}

export function clearToken(): void {
  localStorage.removeItem("clonr_token");
}

function authHeaders(token?: string | null): Record<string, string> {
  const t = token || getStoredToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export async function login(password: string): Promise<AuthResponse> {
  const response = await fetch(`${API_URL}/api/auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Authentication failed" }));
    throw new Error(err.detail || "Authentication failed");
  }
  const data: AuthResponse = await response.json();
  storeToken(data.token);
  return data;
}

export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await fetch(`${API_URL}/api/auth/status`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      throw new Error("Session expired");
    }
    throw new Error("Failed to get auth status");
  }
  return response.json();
}

export interface CloneHistoryItem {
  id: string;
  url: string;
  sandbox_url: string | null;
  preview_url: string | null;
  created_at: string;
}

export interface ClonePaginatedResponse {
  items: CloneHistoryItem[];
  total: number;
  page: number;
  pages: number;
}

export interface CloneFile {
  path: string;
  content: string;
  lines: number;
}

export interface CloneUsage {
  tokens_in: number;
  tokens_out: number;
  total_cost: number;
  api_calls: number;
  model: string;
  duration_s: number;
  agents?: number;
}

export interface CloneEvent {
  status: string;
  message?: string;
  preview_url?: string;
  clone_id?: string;
  files?: CloneFile[];
  scaffold_paths?: string[];
  usage?: CloneUsage;
  // file_write event fields
  type?: string;
  file?: string;
  action?: string;
  lines?: number;
  // screenshot event
  screenshot?: string;
  screenshots?: string[];
  // section_complete / agent progress event fields
  section?: number;
  total?: number;
  components?: string[];
  agent?: number;
  total_agents?: number;
}

// 5 minute timeout for the entire SSE stream
const STREAM_TIMEOUT_MS = 5 * 60 * 1000;
// If no data received for 90s, consider the stream stalled
const STALL_TIMEOUT_MS = 90 * 1000;

export async function startClone(
  url: string,
  onProgress: (data: CloneEvent) => void
): Promise<void> {
  const response = await fetch(`${API_URL}/api/clone`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ url }),
  });

  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Clone request failed" }));
    const detail = error.detail || "Clone request failed";
    // Provide user-friendly messages for common errors
    if (response.status === 422) throw new Error("Invalid URL format. Please check and try again.");
    if (response.status === 429) throw new Error("Too many requests. Please wait a moment and try again.");
    if (response.status >= 500) throw new Error("Server error — the backend may be restarting. Try again in a few seconds.");
    throw new Error(detail);
  }

  if (!response.body) {
    throw new Error("No response body — connection may have been interrupted");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastDataTime = Date.now();
  const streamStart = Date.now();

  while (true) {
    // Check overall timeout
    if (Date.now() - streamStart > STREAM_TIMEOUT_MS) {
      reader.cancel();
      throw new Error("Clone timed out after 5 minutes. The site may be too complex — try a simpler page.");
    }

    // Check stall timeout
    if (Date.now() - lastDataTime > STALL_TIMEOUT_MS) {
      reader.cancel();
      throw new Error("Connection stalled — no data received for 90s. Please try again.");
    }

    const { done, value } = await reader.read();
    if (done) break;

    lastDataTime = Date.now();
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const data = line.slice(6).trim();
        if (data === "[DONE]") return;
        try {
          onProgress(JSON.parse(data));
        } catch {
          console.warn("[clone] Skipped malformed SSE data:", data.slice(0, 100));
        }
      }
    }
  }
}

export async function getClones(page = 1, perPage = 30): Promise<ClonePaginatedResponse> {
  const response = await fetch(`${API_URL}/api/clones?page=${page}&per_page=${perPage}`);
  if (!response.ok) return { items: [], total: 0, page, pages: 0 };
  return response.json();
}

export function getPreviewUrl(cloneId: string): string {
  return `${API_URL}/api/preview/${cloneId}`;
}

export async function getCloneFiles(cloneId: string): Promise<CloneFile[]> {
  try {
    const response = await fetch(`${API_URL}/api/clones/${cloneId}/files`);
    if (!response.ok) return [];
    const data = await response.json();
    return data.files || [];
  } catch {
    return [];
  }
}

export async function endSandbox(cloneId: string): Promise<void> {
  try {
    await fetch(`${API_URL}/api/sandbox/${cloneId}`, { method: "DELETE" });
  } catch {
    // Best-effort cleanup — ignore network errors
  }
}

export function getBeaconEndUrl(cloneId: string): string {
  return `${API_URL}/api/sandbox/${cloneId}/end`;
}

export function resolveApiUrl(path: string): string {
  // If it's already a full URL, return as-is
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  // Relative path from backend — prepend API_URL
  return `${API_URL}${path}`;
}
