// REQ-050-11/12: 공용 폴링 훅
// - 5~15초 간격 자동 갱신
// - 실패 시 마지막 정상 데이터 유지 (비차단)
// - in-flight 중복 방지 (E3)
import { useState, useEffect, useRef, useCallback } from 'react'

export interface PollingState<T> {
  data: T | null
  error: string | null
  isLoading: boolean
  lastUpdatedAt: Date | null
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = 10_000,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)

  // E3: in-flight 중복 방지
  const inFlightRef = useRef(false)
  const mountedRef = useRef(true)

  const poll = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    try {
      const result = await fetcher()
      if (!mountedRef.current) return
      setData(result)
      setError(null)
      setLastUpdatedAt(new Date())
    } catch (err) {
      // REQ-050-12: 실패 시 마지막 데이터 유지, 비차단 오류 표시
      if (!mountedRef.current) return
      setError(err instanceof Error ? err.message : '데이터 로드 실패')
      // data 는 변경하지 않음 — 마지막 정상값 유지
    } finally {
      if (mountedRef.current) {
        setIsLoading(false)
      }
      inFlightRef.current = false
    }
  }, [fetcher])

  useEffect(() => {
    mountedRef.current = true
    poll()
    const id = setInterval(poll, intervalMs)
    return () => {
      mountedRef.current = false
      clearInterval(id)
    }
  }, [poll, intervalMs])

  return { data, error, isLoading, lastUpdatedAt }
}
