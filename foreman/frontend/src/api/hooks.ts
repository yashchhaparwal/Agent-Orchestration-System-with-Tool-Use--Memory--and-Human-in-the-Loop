import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from './client'
import { TERMINAL, toSteps, toTaskTrace } from './adapters'
import type { TaskTrace } from '@/types/trace'
import type { ApprovalStatus, DecideRequest } from '@/types/approval'

// ── Health ────────────────────────────────────────────────────────────────────
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => apiClient.health().then((r) => r.data),
    refetchInterval: 10_000,
  })
}

// ── Tasks ─────────────────────────────────────────────────────────────────────
export function useRunTask() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: apiClient.createTask,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['traces'] }),
  })
}

/** Follows one task until it reaches a final state. Pass `false` to stop polling. */
export function useTask(taskId: string | undefined, intervalMs: number | false = 3_000) {
  return useQuery({
    queryKey: ['task', taskId],
    queryFn: () => apiClient.getTask(taskId!).then((r) => r.data),
    enabled: !!taskId,
    refetchInterval: (q) =>
      q.state.data && TERMINAL.includes(q.state.data.status) ? false : intervalMs,
  })
}

export function useRecentTasks() {
  return useQuery({
    queryKey: ['traces', 'recent'],
    queryFn: () => apiClient.listTasks().then((r) => r.data),
    refetchInterval: 10_000,
  })
}

// ── Outbox & Memory ───────────────────────────────────────────────────────────
export function useOutbox() {
  return useQuery({
    queryKey: ['outbox'],
    queryFn: () => apiClient.listOutbox().then((r) => r.data),
    refetchInterval: 10_000,
  })
}

export function useMemoryUsers() {
  return useQuery({
    queryKey: ['memory-users'],
    queryFn: () => apiClient.listMemoryUsers().then((r) => r.data),
  })
}

export function useMemories(userId: string) {
  return useQuery({
    queryKey: ['memories', userId],
    queryFn: () => apiClient.listMemories(userId).then((r) => r.data),
    enabled: !!userId,
  })
}

export function useDeleteMemories() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => apiClient.deleteMemories(userId).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memories'] })
      qc.invalidateQueries({ queryKey: ['memory-users'] })
    },
  })
}

// ── HITL Review queue ─────────────────────────────────────────────────────────

export function useApprovals(status: ApprovalStatus | 'all' = 'pending') {
  return useQuery({
    queryKey: ['approvals', status],
    queryFn: () => apiClient.listApprovals(status).then((r) => r.data),
    refetchInterval: 5_000,
  })
}

/** Recording a decision wakes the worker, so tasks and traces change too. */
export function useDecide() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: DecideRequest & { id: number }) =>
      apiClient.decide(id, body).then((r) => r.data),
    // also on failure: a 409 means someone else already decided, so the queue is stale
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['approvals'] })
      qc.invalidateQueries({ queryKey: ['task'] })
      qc.invalidateQueries({ queryKey: ['traces'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

// ── Runs / Traces ─────────────────────────────────────────────────────────────

// Finished tasks never change, so each is fetched in full only once.
const finished = new Map<string, TaskTrace>()

export function useTraces() {
  return useQuery({
    queryKey: ['traces'],
    queryFn: async () => {
      const { data: summaries } = await apiClient.listTasks()
      return Promise.all(
        summaries.map(async (t) => {
          const cached = finished.get(t.task_id)
          if (cached && cached.backend_status === t.status) return cached
          const { data } = await apiClient.getTask(t.task_id)
          const trace = toTaskTrace(data)
          if (TERMINAL.includes(data.status)) finished.set(t.task_id, trace)
          return trace
        }),
      )
    },
    refetchInterval: 5_000,
  })
}

export function useTaskSpans(trace: TaskTrace | undefined) {
  return useQuery({
    queryKey: ['spans', trace?.run_id, trace?.backend_status],
    queryFn: async () => {
      const { data } = await apiClient.getTrace(trace!.run_id)
      return {
        source: data.source,
        raw: data.spans,
        spans: toSteps(data.spans, trace!.outputs_by_subtask),
      }
    },
    enabled: !!trace,
    refetchInterval: trace && TERMINAL.includes(trace.backend_status) ? false : 5_000,
  })
}

export function useStats(days: number) {
  return useQuery({
    queryKey: ['stats', days],
    queryFn: () => apiClient.getStats(days).then((r) => r.data),
    refetchInterval: 10_000,
    placeholderData: (previous) => previous,
  })
}

export function useCheckpoints(taskId: string) {
  return useQuery({
    queryKey: ['checkpoints', taskId],
    queryFn: () => apiClient.getCheckpoints(taskId).then((r) => r.data),
  })
}

export function useReplayTask() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ taskId, checkpointId, overrides }: {
      taskId: string
      checkpointId: string
      overrides: Record<string, unknown>
    }) => apiClient.replayTask(taskId, checkpointId, overrides).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['traces'] }),
  })
}

export function useReplayDiff(forkTaskId: string) {
  return useQuery({
    queryKey: ['replay-diff', forkTaskId],
    queryFn: () => apiClient.getReplayDiff(forkTaskId).then((r) => r.data),
    // the fork runs in the background, so the comparison keeps moving
    refetchInterval: 5_000,
  })
}
