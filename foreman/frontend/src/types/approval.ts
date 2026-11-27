// Mirrors packages/shared/types/approval.py and ApprovalStore.view — keep in sync.

export type ApprovalLevel = 'L1' | 'L2' | 'L3' | 'L4'

export type ApprovalKind = 'tool_call' | 'plan' | 'escalation'

export type ApprovalStatus =
  | 'pending'
  | 'approved'
  | 'modified'
  | 'rejected'
  | 'taken_over'
  | 'expired'

export type ApprovalTrigger =
  | 'sensitive_tool_call'
  | 'low_plan_confidence'
  | 'user_requested_review'
  | 'repeated_failure'
  | 'low_review_score'

export type DecisionKind = 'approve' | 'modify' | 'reject' | 'take_over'

export interface PlanSubtask {
  id: string
  specialist: string
  description: string
  depends_on: string[]
  status?: string
  expected_output?: string
}

/** Snapshot of the run taken at the moment it paused. */
export interface ApprovalContext {
  request?: string
  user_id?: string
  plan?: PlanSubtask[]
  plan_confidence?: number | null
  completed_subtasks?: Array<{
    id: string
    attempt: number
    status: string
    score: number | null
    output_preview: string
    sources: string[]
  }>
  memories?: Array<Record<string, unknown>>
  error?: string | null
}

export interface ProposedAction {
  // tool_call
  tool?: string
  arguments?: Record<string, unknown>
  risk?: string | null
  gate_reason?: string
  other_pending?: string[]
  // plan
  plan?: { subtasks: PlanSubtask[]; rationale?: string; confidence?: number }
  // escalation
  reason?: string
  rejected_subtasks?: string[]
}

export interface ApprovalRequest {
  id: number
  task_id: string
  subtask_id: string | null
  attempt: number
  kind: ApprovalKind
  level: ApprovalLevel
  trigger: ApprovalTrigger
  agent: string
  proposed_action: ProposedAction
  reasoning: string
  context: ApprovalContext
  status: ApprovalStatus
  decision: DecisionKind | null
  decided_by: string | null
  reason: string | null
  created_at: string
  decided_at: string | null
  expires_at: string | null
}

export interface DecideRequest {
  decision: DecisionKind
  payload?: Record<string, unknown>
  reason?: string
  decided_by?: string
}

/** Human-readable copy for each approval level, used by the review UI. */
export const APPROVAL_LEVEL_META: Record<
  ApprovalLevel,
  { label: string; blurb: string; tone: 'info' | 'warn' | 'danger' }
> = {
  L1: {
    label: 'Notify',
    blurb: 'Informational only — the run continued without waiting.',
    tone: 'info',
  },
  L2: {
    label: 'Approve action',
    blurb: 'Confirm this single tool call before the run continues.',
    tone: 'warn',
  },
  L3: {
    label: 'Approve plan',
    blurb: 'Review the full execution plan before any work begins.',
    tone: 'warn',
  },
  L4: {
    label: 'Take over',
    blurb: 'The agents failed repeatedly. Approve retries once more; or fix, take over, or cancel.',
    tone: 'danger',
  },
}

export const TRIGGER_LABELS: Record<ApprovalTrigger, string> = {
  sensitive_tool_call: 'Sensitive tool call',
  low_plan_confidence: 'Low plan confidence',
  user_requested_review: 'User requested',
  repeated_failure: 'Repeated failure',
  low_review_score: 'Low review score',
}
