// AC-M2-2: usePolling 단위 테스트 (실제 타이머 + 작은 interval)
// - 폴링 간격 후 fetcher 재호출
// - 실패 시 마지막 정상 데이터 유지 + 비차단 오류 표시
// - in-flight 중복 방지 (E3)
// NOTE: vi.useFakeTimers() + RTL waitFor 는 데드락(waitFor 가 실제 타이머에 의존)이므로
//       실제 타이머와 짧은 interval 로 검증한다.
import { renderHook, waitFor, act } from '@testing-library/react'
import { vi, describe, it, expect, afterEach } from 'vitest'
import { usePolling } from '../hooks/usePolling'

afterEach(() => { vi.restoreAllMocks() })

describe('usePolling', () => {
  it('초기 로드 시 data 를 채우고 isLoading 을 false 로 바꾼다', async () => {
    const fetcher = vi.fn().mockResolvedValue({ value: 42 })
    const { result } = renderHook(() => usePolling(fetcher, 10_000))

    expect(result.current.isLoading).toBe(true)

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toEqual({ value: 42 })
    expect(result.current.error).toBeNull()
    expect(result.current.lastUpdatedAt).toBeInstanceOf(Date)
  })

  it('interval 경과 후 fetcher 를 재호출한다', async () => {
    const fetcher = vi.fn().mockResolvedValue('ok')
    renderHook(() => usePolling(fetcher, 40))

    await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('fetcher 실패 시 마지막 정상 data 를 유지하고 error 를 설정한다 (REQ-050-12)', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce('first-ok')
      .mockRejectedValue(new Error('HTTP 503'))

    const { result } = renderHook(() => usePolling(fetcher, 40))

    await waitFor(() => expect(result.current.data).toBe('first-ok'))
    await waitFor(() => expect(result.current.error).toMatch(/503/))

    // 마지막 정상 데이터 유지
    expect(result.current.data).toBe('first-ok')
    expect(result.current.isLoading).toBe(false)
  })

  it('in-flight 중 추가 호출은 무시한다 (E3)', async () => {
    let resolveFirst!: (v: string) => void
    const slowPromise = new Promise<string>((res) => { resolveFirst = res })
    const fetcher = vi.fn().mockReturnValueOnce(slowPromise).mockResolvedValue('fast')

    const { result } = renderHook(() => usePolling(fetcher, 20))

    // 첫 호출이 진행 중인 동안 여러 interval 이 지나도 중복 호출 안 됨
    await new Promise((r) => setTimeout(r, 90))
    expect(fetcher).toHaveBeenCalledTimes(1)

    // 첫 호출 완료
    await act(async () => { resolveFirst('slow-result') })
    await waitFor(() => expect(result.current.data).toBe('slow-result'))
  })

  it('언마운트 후 상태 업데이트를 하지 않는다', async () => {
    let resolve!: (v: string) => void
    const promise = new Promise<string>((res) => { resolve = res })
    const fetcher = vi.fn().mockReturnValue(promise)

    const { unmount } = renderHook(() => usePolling(fetcher, 10_000))

    unmount()
    await act(async () => { resolve('after-unmount') })
    // 에러 없이 조용히 무시돼야 함
  })
})
