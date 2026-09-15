export const tokenKey = "topgreencloud-token";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request(path: string, options: RequestInit = {}, token?: string): Promise<Response> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (typeof options.body === "string") headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${(process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "")}${path}`, {
      ...options, headers, credentials: "omit", cache: "no-store",
    });
  } catch { throw new ApiError(0, "Unable to reach the service. Please try again."); }
  if (!response.ok) {
    const messages: Record<number, string> = {
      401: "Sign-in failed or your session expired. Please sign in again.",
      404: "The requested record is not available.",
      409: "This email is already registered. Please sign in.",
      413: "The file exceeds the upload or parsing limit.",
      415: "Please choose a CSV or PDF file with the correct file type.",
      422: "Check your input. Bills must be structured CSV or readable, unencrypted text PDFs.",
    };
    throw new ApiError(response.status, messages[response.status] || "The request could not be completed. Please try again.");
  }
  return response;
}

export async function api<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await request(path, options, token);
  return response.status === 204 ? undefined as T : response.json();
}

export async function apiBlob(path: string, token: string): Promise<{ blob: Blob; filename: string }> {
  const response = await request(path, {}, token);
  const disposition = response.headers.get("Content-Disposition") || "";
  const analysisId = path.match(/\/history\/(\d+)\/export\.csv$/)?.[1];
  const filename = disposition.match(/filename="([A-Za-z0-9._-]+)"/)?.[1] || `topgreencloud-analysis-${analysisId || "export"}.csv`;
  return { blob: await response.blob(), filename };
}

export function safeLink(value: string | null): string | undefined {
  if (!value) return;
  try { const url = new URL(value); if (["https:", "http:"].includes(url.protocol)) return url.href; } catch { /* Unusable stored link. */ }
}

export interface Provider {
  id: number; name: string; slug: string; website_url: string | null; description: string | null;
  metrics?: { metric_name: string; metric_value: string; unit: string | null; source_url: string; source_date: string | null; notes: string | null }[];
}
export interface User { id: number; email: string; is_active: boolean; is_admin: boolean; created_at: string }
export interface Usage {
  service_name: string | null; service_category: string | null; region: string | null;
  usage_quantity: string | null; usage_unit: string | null;
}
export interface Bill {
  bill_id: number | null; filename: string; file_type: string; provider: string | null; row_count: number;
  items: (Usage & { cost: string | null; currency: string | null; billing_period: string | null })[]; warnings: string[];
}
export interface Carbon {
  analysis_id: number | null; total_kg_co2e: string | null; methodology_version: string; complete: boolean; warnings: string[];
  calculated_items: (Usage & { provider: string | null; estimated_kg_co2e: string; source: { name: string; url: string; methodology_version: string } })[];
  unsupported_items: (Usage & { provider: string | null; reason: string })[];
}
export interface StoredBill {
  id: number; original_filename: string; file_type: string; file_size: number; status: string;
  uploaded_at: string; provider_slug: string | null;
}
export interface AnalysisHistory {
  analysis_id: number; bill_id: number; bill_filename: string; provider_slug: string | null;
  total_kg_co2e: string | null; methodology_version: string; complete: boolean; created_at: string;
}
