export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function api<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, {
      method: options.method || "GET",
      credentials: "same-origin",
      redirect: "error",
      headers: {
        Accept: "application/json",
        ...(options.body === undefined
          ? {}
          : { "Content-Type": "application/json" }),
        ...(options.method && options.method !== "GET"
          ? { "X-CSRF-Token": csrf }
          : {}),
        ...options.headers,
      },
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(
      "SERVICE_UNAVAILABLE",
      "无法连接服务，请检查 API 后重试。",
      503,
    );
  }
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      data?.error?.code || "INVALID_RESPONSE",
      data?.error?.message || "服务未返回有效结果。",
      response.status,
    );
  if (data === null)
    throw new ApiError("INVALID_RESPONSE", "服务响应格式无效。", 502);
  return data as T;
}
