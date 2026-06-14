// 에러 격리: 한 패널/컴포넌트가 런타임 에러로 죽어도 전체 앱이 블랭크되지 않도록
// React Error Boundary 로 감싼다. (REQ-050-12 비차단 원칙의 렌더 레벨 보강)
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
            border: '1px solid #f8514955',
            background: '#161b22',
            color: '#f85149',
            padding: '16px',
            borderRadius: 8,
            margin: 8,
            fontSize: '0.85rem',
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6 }}>
            {this.props.label ?? '패널'} 렌더 오류 (나머지 대시보드는 정상)
          </div>
          <div style={{ color: '#8b949e', fontFamily: 'monospace', fontSize: '0.75rem' }}>
            {this.state.message}
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
