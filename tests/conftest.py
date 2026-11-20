"""테스트 공통 설정.

WeasyPrint는 PDF를 만들 때만 쓰고 시스템 라이브러리(pango)가 있어야 불러올 수 있다.
그래프 구조만 보는 테스트에서는 필요 없으므로, 불러오지 못하면 빈 대역으로 바꾼다.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import weasyprint  # noqa: F401
except Exception:
    fake = types.ModuleType("weasyprint")
    fake.HTML = object
    fake.CSS = object
    sys.modules["weasyprint"] = fake
