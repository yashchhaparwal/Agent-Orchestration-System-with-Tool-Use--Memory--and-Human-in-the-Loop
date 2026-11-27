import MetricsDashboard from '@/components/traces/MetricsDashboard'
import { Page } from '@/components/ui'

export default function StatsPage() {
  return (
    <Page title="Stats" subtitle="Operational numbers, safety, and the last eval headline">
      <MetricsDashboard />
    </Page>
  )
}
