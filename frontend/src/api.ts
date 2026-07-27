// API client — credentials included (session cookie), errors throw {detail}.
const BASE = "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.status = status;
  }
}

export async function api<T = any>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && opts.body instanceof FormData;
  const headers: Record<string, string> = {};
  if (!isFormData) {
    headers["Content-Type"] = "application/json";
  }
  if (opts.headers) {
    const h = new Headers(opts.headers);
    h.forEach((v, k) => { headers[k] = v; });
  }
  const r = await fetch(BASE + path, {
    ...opts,
    credentials: "include",
    headers,
  });
  if (r.status === 204) return undefined as T;
  let body: any = null;
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("json")) body = await r.json();
  else body = await r.text();
  if (!r.ok) {
    const detail =
      (body && typeof body === "object" && body.detail) || String(body) || `HTTP ${r.status}`;
    throw new ApiError(r.status, detail);
  }
  return body as T;
}

export function nowHHMM(d = new Date()) {
  return d.toLocaleTimeString("en-GB", { hour12: false }).slice(0, 8);
}