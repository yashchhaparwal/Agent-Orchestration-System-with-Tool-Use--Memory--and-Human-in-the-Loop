import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from '@/components/Layout'
import ReviewPage from '@/pages/ReviewPage'
import TracesPage from '@/pages/TracesPage'
import TasksPage from '@/pages/TasksPage'
import OutboxPage from '@/pages/OutboxPage'
import MemoryPage from '@/pages/MemoryPage'
import StatsPage from '@/pages/StatsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/review" replace />} />
          <Route path="review" element={<ReviewPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="traces" element={<TracesPage />} />
          <Route path="outbox" element={<OutboxPage />} />
          <Route path="memory" element={<MemoryPage />} />
          <Route path="stats" element={<StatsPage />} />
          {/* Catch-all */}
          <Route path="*" element={<Navigate to="/review" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
