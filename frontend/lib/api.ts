const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface CloneHistoryItem {
  id: string;
  url: string;
  sandbox_url: string | null;
  created_at: string;
}

export interface CloneFile {
  path: string;
  content: string;
  lines: number;
}

export interface CloneEvent {
  status: string;
  message?: string;
  preview_url?: string;
  clone_id?: string;
  files?: CloneFile[];
  // file_write event fields
  type?: string;
  file?: string;
  action?: string;
  lines?: number;
  // screenshot event
  screenshot?: string;
}

export async function startClone(
  url: string,
  onProgress: (data: CloneEvent) => void
): Promise<void> {
  const response = await fetch(`${API_URL}/api/clone`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });

  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Clone request failed" }));
    throw new Error(error.detail || "Clone request failed");
  }

  if (!response.body) {
    throw new Error("No response body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

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
          // skip malformed JSON
        }
      }
    }
  }
}

export async function getClones(): Promise<CloneHistoryItem[]> {
  const response = await fetch(`${API_URL}/api/clones`);
  if (!response.ok) return [];
  return response.json();
}

export function getPreviewUrl(cloneId: string): string {
  return `${API_URL}/api/preview/${cloneId}`;
}

export function resolveApiUrl(path: string): string {
  // If it's already a full URL, return as-is
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  // Relative path from backend — prepend API_URL
  return `${API_URL}${path}`;
}
