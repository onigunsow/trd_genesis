/**
 * SPEC-065 REQ-065-2b — "수정 이후만" 토글의 단일 상태.
 *
 * 개요 헤드라인·게이트 뷰·거래원장이 같은 since 를 써야 화면끼리 숫자가 어긋나지
 * 않는다. 기준일은 /api/status.gate.since (env DASHBOARD_GATE_SINCE) 에서 오고
 * 프런트는 절대 날짜 리터럴을 갖지 않는다. gate.since 가 null 이면 토글은 비활성.
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import type { GateConfig } from '../api/types'

interface GateState {
  config: GateConfig | null      // 서버 설정 (null = 아직 로드 전)
  enabled: boolean               // 토글 ON/OFF
  since: string | null           // 실제로 API 에 넘길 값 (enabled && config.since)
  setEnabled: (v: boolean) => void
}

const Ctx = createContext<GateState>({
  config: null, enabled: false, since: null, setEnabled: () => {},
})

export function GateProvider({ config, children }: { config: GateConfig | null; children: ReactNode }) {
  // 기본 ON — 계좌 리셋 전후를 섞어 보여주는 게 사고다. 전기간은 운영자가 명시적으로 끈다.
  const [enabled, setEnabled] = useState(true)
  const value = useMemo<GateState>(() => ({
    config,
    enabled,
    since: enabled && config?.since ? config.since : null,
    setEnabled,
  }), [config, enabled])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useGate(): GateState {
  return useContext(Ctx)
}

/** 헤더에 놓는 토글. 서버 설정이 없으면 비활성 + 이유를 title 로. */
export function GateToggle() {
  const { config, enabled, setEnabled } = useGate()
  const available = !!config?.since
  return (
    <label
      title={available
        ? `${config!.since} 이후만 집계 (${config!.source === 'account_switch' ? '모의계좌 리셋 기준' : 'DASHBOARD_GATE_SINCE'})`
        : 'DASHBOARD_GATE_SINCE 미설정·ACCOUNT_SWITCH 없음 — 게이트 필터 비활성'}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        fontSize: '0.72rem', color: available ? 'var(--text-secondary)' : 'var(--text-muted)',
        cursor: available ? 'pointer' : 'not-allowed', userSelect: 'none',
      }}
    >
      <input
        type="checkbox"
        checked={enabled}
        disabled={!available}
        onChange={e => setEnabled(e.target.checked)}
        aria-label="수정 이후 진입분만 집계"
      />
      {config?.source === 'account_switch' ? '계좌 리셋 이후만' : '수정 이후만'}{available ? ` (${config!.since}~)` : ''}
    </label>
  )
}
