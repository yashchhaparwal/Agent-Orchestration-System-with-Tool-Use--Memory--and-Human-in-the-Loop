/// <reference types="vite/client" />
import axios from 'axios'
import type {
  MemoryUser, OutboxEntry, RunTaskRequest, StoredMemory, TaskSummary, TaskView,
} from '@/types'
import type { Checkpoint, RawTrace, Stats, TraceDiff } from '@/types/trace'
import type { ApprovalRequest, ApprovalStatus, DecideRequest } from '@/types/approval'


// Base client — proxied to the Foreman API through /api (vite.config.ts), which adds X-API-Key
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? '/api',
  headers: { 'Content-Type': 'application/json' },
  timeout: 60_000,
})

// ── Response interceptor — surface the API's problem+json messages ─────────
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail = err.response?.data?.detail
    const message = Array.isArray(detail)
      ? detail.map((d: { loc?: string[]; msg?: string }) => `${d.loc?.join('.')}: ${d.msg}`).join('; ')
      : detail ?? err.message ?? 'Unknown error'
    return Promise.reject(new Error(message))
  },
)

// ── API methods ────────────────────────────────────────────────────────────

export const apiClient = {
  health: () => api.get<{ status: string }>('/health'),

  // ── Tasks ─────────────────────────────────────────────────────────────
  /** Queues the task and returns at once; poll getTask for progress. */
  createTask: (req: RunTaskRequest) =>
    api.post<{ task_id: string; status: string }>('/v1/tasks', req),

  listTasks: (limit = 30) =>
    api.get<TaskSummary[]>('/v1/tasks', { params: { limit } }),

  getTask: (taskId: string) => api.get<TaskView>(`/v1/tasks/${taskId}`),

  // ── HITL Review ───────────────────────────────────────────────────────
  listApprovals: (status: ApprovalStatus | 'all' = 'pending') =>
    api.get<ApprovalRequest[]>('/v1/approvals', {
      // an empty status means "every status"
      params: { status: status === 'all' ? '' : status, limit: 500 },
    }),

  decide: (approvalId: number, body: DecideRequest) =>
    api.post<ApprovalRequest>(`/v1/approvals/${approvalId}/decide`, body),

  // ── Outbox & Memory ───────────────────────────────────────────────────
  listOutbox: (limit = 200) => api.get<OutboxEntry[]>('/v1/outbox', { params: { limit } }),

  listMemoryUsers: () => api.get<MemoryUser[]>('/v1/memory/users'),

  listMemories: (userId: string) =>
    api.get<StoredMemory[]>(`/v1/memory/users/${encodeURIComponent(userId)}`),

  deleteMemories: (userId: string) =>
    api.delete<{ user_id: string; deleted: number }>(
      `/v1/memory/users/${encodeURIComponent(userId)}`,
    ),

  // ── Traces & Observability ────────────────────────────────────────────
  getTrace: (taskId: string) => api.get<RawTrace>(`/v1/tasks/${taskId}/trace`),

  getStats: (days = 30) => api.get<Stats>('/v1/stats', { params: { days } }),

  getCheckpoints: (taskId: string) =>
    api.get<Checkpoint[]>(`/v1/tasks/${taskId}/checkpoints`),

  replayTask: (taskId: string, checkpointId: string, overrides: Record<string, unknown>) =>
    api.post<{ task_id: string }>(`/v1/tasks/${taskId}/replay`, {
      checkpoint_id: checkpointId,
      overrides,
    }),

  getReplayDiff: (forkTaskId: string) => api.get<TraceDiff>(`/v1/tasks/${forkTaskId}/diff`),
}
