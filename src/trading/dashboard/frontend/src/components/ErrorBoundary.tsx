// REQ-054-B4: 에러 격리 — 한 패널의 런타임 오류가 전체 화면을 검게 만들지 않도록
// React Error Boundary 로 각 주요 패널을 개별 격리
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  label?: string
}
interface State {
  hasError: boolean
  message: string
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(err: unknown): State {
    return { hasError: true, message: err instanceof Error ? err.message : String(err) }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 콘솔에만 기록 — 전체 앱은 계속 동작
    console.error('[ErrorBoundary]', this.props.label ?? '', error, info)
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div
          style={{
            border: '1px solid #fca5a5',
            background: '#fef2f2',
            color: '#cf222e',
            padding: '16px',
            borderRadius: 8,
            margin: 8,
            fontSize: '0.85rem',
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>
            {this.props.label ?? '패널'} 렌더 오류 (나머지 대시보드는 정상)
          </div>
          <div style={{ color: '#656d76', fontFamily: 'monospace', fontSize: '0.75rem' }}>
            {this.state.message}
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
