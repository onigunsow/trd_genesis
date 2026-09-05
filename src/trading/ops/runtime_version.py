"""실행 중인 코드·프롬프트 버전을 시간축에 남긴다 (SPEC 없음, 2026-09-05).

성과 시계열을 "어느 코드가 낸 것인가" 로 가르기 위한 최소 장치다. 2026-09-05 에
60일 창이 서로 다른 체제를 섞는 바람에 이미 고쳐진 결함 셋을 현재 결함으로
진단했다(장전 매도·rotate·원장 음수). 사람이 배포 이력을 대조해 겨우 걸렀고,
주간 자동 에이전트에는 그 제동이 없다.

``HOST_BUILD_COMMIT`` 을 쓰지 않는 이유: compose 가 주입하지만 ``make redeploy``
경로에서만 채워지고(그 외에는 "unknown"), 무엇보다 ``src/`` 가 bind-mount 라
이미지 커밋이 실제 실행 중인 소스와 다를 수 있다. 실행되는 파일을 직접 해시한다.
"""

from __future__ import annotations

import functools
import hashlib
import logging
from pathlib import Path

from trading.db.session import connection

LOG = logging.getLogger(__name__)


def _code_dir() -> Path:
    """``src/trading`` 루트 (이 파일의 조부모)."""
    return Path(__file__).resolve().parent.parent


@functools.lru_cache(maxsize=1)
def code_set_hash() -> str | None:
    """``src/trading/**/*.py`` 전체 내용의 md5 앞 12자. 산출 불가면 None.

    파일 하나만 바뀌어도 값이 바뀐다. 경로를 해시에 넣어 파일 추가·삭제·이름 변경도
    새 해시가 되게 한다. ``__pycache__`` 는 제외한다 — ``.pyc`` 는 ``*.py`` 에 애초에
    안 걸리지만, 그 디렉터리에 생성된 ``.py`` 가 섞이면 같은 소스가 다른 버전으로
    갈리므로 경로 자체를 막는다.

    한계 — 이 해시가 덮지 못하는 체제 변화가 있다:
      - ``.sql`` 마이그레이션(스키마·데이터 교정)
      - ``src/`` 밖의 설정(``.moai/config/**``: 2026-08-23 사이징 파라미터가 여기서 바뀌었다)
      - env 값(2026-09-05 TRAIL_ARM_PCT 5.0 -> 12.0 이 .env 에서 바뀌었다)
    "코드 체제" 라는 라벨은 실제 체제 변화의 일부만 덮는다.

    또 하나 — ``src/`` 가 bind-mount 라 이 해시는 "호출 시점 디스크의 코드" 이지
    "이 프로세스가 실제로 로드한 코드" 가 아니다. 진입점 최상단에서 부르는 이유이며,
    프로세스 기동과 첫 호출 사이에 소스가 바뀌면 창이 남는다(없앨 수 없다).

    프로세스 수명 동안 캐시된다(코드를 고치면 재시작이 따라온다).
    """
    try:
        root = _code_dir()
        h = hashlib.md5(usedforsecurity=False)  # 버전 표식, 보안 용도 아님
        n = 0
        for f in sorted(root.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            h.update(str(f.relative_to(root)).encode())
            h.update(f.read_bytes())
            n += 1
        if n == 0:
            LOG.warning("code_set_hash: *.py 가 없다 — 버전 기록 생략")
            return None
        return h.hexdigest()[:12]
    except Exception:  # 버전 표식 산출 실패가 매매를 막아서는 안 된다.
        LOG.warning("code_set_hash 산출 실패", exc_info=True)
        return None


# INSERT 가 성공한 뒤에만 True. lru_cache 를 쓰지 않는 이유가 여기 있다 —
# lru_cache 는 반환값을 함수가 끝난 *뒤* 저장하므로, DB 가 죽어 있을 때의 결과가
# 그대로 캐시되고 except 안에서 cache_clear() 를 불러도 소용이 없다(실측). 장기 실행
# 스케줄러가 postgres 보다 먼저 뜬 한 번의 레이스로 몇 주치 기록이 통째로 비게 된다.
_recorded = False


def record_runtime_version() -> tuple[str | None, str | None]:
    """현재 (code_hash, prompt_hash) 를 ``runtime_version`` 에 기록한다.

    같은 조합이 이미 있으면 ``last_seen`` 만 갱신한다 — ``first_seen`` 은 최초 관측
    시각이라야 구간이 성립하고, ``last_seen`` 이 있어야 롤백·재배포로 같은 버전이
    두 번 도는 구간을 잃지 않는다. 그것이 없으면 재등장 이후 성과가 전부 다음 버전
    것으로 조용히 귀속된다.

    성공할 때까지 매 호출 시도한다(성공 후에는 no-op). 실패해도 예외를 올리지 않는다 —
    관측 손실이 스케줄러 기동이나 매매를 막아서는 안 된다.
    """
    global _recorded
    from trading.personas.base import prompt_set_hash

    code = code_set_hash()
    prompt = prompt_set_hash()  # 자체적으로 실패를 삼키고 None 을 돌려준다
    if _recorded:
        return code, prompt

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runtime_version (code_hash, prompt_hash, last_seen)
                VALUES (%s, %s, now())
                ON CONFLICT (COALESCE(code_hash, ''), COALESCE(prompt_hash, ''))
                DO UPDATE SET last_seen = now()
                """,
                (code, prompt),
            )
        _recorded = True
    except Exception:  # 기록 실패는 관측 손실일 뿐 — 매매는 계속한다.
        LOG.warning("runtime_version 기록 실패", exc_info=True)

    return code, prompt
