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
    새 해시가 되게 한다. ``__pycache__`` 는 제외한다 — 소스가 같아도 바이트코드는
    파이썬 버전·실행 시각에 따라 달라져 같은 코드가 다른 버전으로 갈린다.

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


@functools.lru_cache(maxsize=1)
def record_runtime_version() -> tuple[str | None, str | None]:
    """현재 (code_hash, prompt_hash) 를 ``runtime_version`` 에 한 번 기록한다.

    같은 조합이 이미 있으면 아무 것도 하지 않는다(``first_seen`` 은 최초 관측 시각을
    유지해야 구간 조인이 성립한다). 프로세스당 1회 — lru_cache 가 그 보장이다.

    반환값은 기록한 (code_hash, prompt_hash). 실패해도 예외를 올리지 않는다.
    """
    from trading.personas.base import prompt_set_hash

    code = code_set_hash()
    try:
        prompt = prompt_set_hash()
    except Exception:  # 프롬프트 해시 실패가 코드 해시 기록까지 막지 않게.
        LOG.warning("prompt_set_hash 실패 — code_hash 만 기록", exc_info=True)
        prompt = None

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runtime_version (code_hash, prompt_hash)
                VALUES (%s, %s)
                ON CONFLICT (COALESCE(code_hash, ''), COALESCE(prompt_hash, ''))
                DO NOTHING
                """,
                (code, prompt),
            )
    except Exception:  # 기록 실패는 관측 손실일 뿐 — 매매는 계속한다.
        LOG.warning("runtime_version 기록 실패", exc_info=True)

    return code, prompt
