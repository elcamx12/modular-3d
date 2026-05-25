"""프로젝트 설정 — 사업 전체에 공통으로 쓰이는 입력을 한곳에서 받는 모달 창.

[정책 2026-05-24 프로젝트 설정 1단계]
- 여러 설계안(케이스)을 비교하려면 모든 안에서 똑같이 고정돼야 하는 공통
  조건(운송 거리·운임·비내력벽 단위중량·현장 지역·착공일 등)을, 각 탭의
  입력 자리에서 빼내어 여기서 한 번만 받는다.
- 이번 단계는 **창 + 세션 메모리 보관까지만**. 운송 탭 등 소비처와의 실제
  연결(탭 입력칸 제거 + 이 값 읽어오기)은 다음 단계로 미룬다. 따라서 본
  모듈은 어떤 해석/물량/운송 로직도 호출하지 않는다.
- 저장은 파일이 아니라 세션 메모리(ProjectSettings 객체)뿐. 프로그램을 끄면
  초기화된다.

[필드 출처]
- 운송(편도거리·왕복·운임방식·트럭 종류별 km단가)·비내력벽 단위중량(내30/
  외55): 운송 탭(transport_panel.py)의 위젯과 매핑. 운임은 거리(km) 기반 두
  방식(요금표 자동 / 트레일러별 km단가)만 쓴다(시간단가 제거).
- 공기(현장 지역·별표1/별표2 비작업일·착공일): 팀원 공정표 프로그램
  (Song-Jung-Hun/modular-schedule 의 모듈러주택_공정표.html)의 'D. 지역
  비작업일 기준' 입력을 반영. 지역 선택 시 별표1/별표2 자동 채움.
"""
from dataclasses import dataclass

from PyQt5.QtCore import QDate
from PyQt5.QtWidgets import (
    QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QPushButton, QSpinBox,
    QVBoxLayout,
)


# ── 지역별 LH 지침 비작업일(별표1·별표2) 기준 ──────────────────────
# (지역그룹, 도시 라벨, 별표1=기초공사 비작업일/년, 별표2=골조공사 비작업일/년)
# 출처: 팀원 공정표 프로그램. 도시 선택 시 별표1/별표2 가 자동으로 채워진다.
CITY_HOLIDAYS = [
    ("수도권", "서울", 170, 158),
    ("수도권", "인천", 170, 156),
    ("수도권", "수원 경기남부", 170, 163),
    ("수도권", "파주 경기북부", 189, 175),
    ("강원", "춘천", 185, 176),
    ("강원", "원주", 188, 177),
    ("강원", "강릉", 163, 153),
    ("충청", "대전", 175, 162),
    ("충청", "천안 충남", 177, 167),
    ("충청", "청주 충북", 184, 173),
    ("충청", "서산 충남", 169, 158),
    ("경상", "대구", 165, 161),
    ("경상", "부산", 174, 150),
    ("경상", "창원", 167, 155),
    ("경상", "울산", 159, 153),
    ("전라", "광주", 179, 174),
    ("전라", "전주", 176, 174),
    ("제주", "제주", 178, 160),
]

# 기본 선택 도시 — 공정표 프로그램 기본값과 동일.
_DEFAULT_CITY = "천안 충남"


@dataclass
class ProjectSettings:
    """사업 공통 설정 — 세션 메모리에만 보관되는 값들.

    각 필드 기본값은 현재 운송 탭/공정표 프로그램의 기본값과 일치시킨다.
    """
    # 운송
    distance_km: float = 30.0           # 편도 거리 (운임은 항상 왕복=편도×2)
    cost_mode: str = "freight_table"    # 'freight_table' | 'per_km'
    lowbed_per_km_krw: float = 3500.0   # 저상/초저상 km단가 (per_km 방식)
    extendable_per_km_krw: float = 5000.0  # 광폭(확장형) km단가
    aframe_per_km_krw: float = 5000.0   # A-frame km단가
    # 비내력벽 단위중량
    wall_interior_kg_m2: float = 30.0   # 내부 단위중량
    wall_exterior_kg_m2: float = 55.0   # 외부 단위중량
    # 공기(현장 지역 비작업일·착공일)
    region_city: str = _DEFAULT_CITY    # 현장 지역(도시 라벨)
    nonwork_days_foundation: int = 177  # 별표1 — 기초공사 비작업일/년
    nonwork_days_frame: int = 167       # 별표2 — 골조공사 비작업일/년
    start_date: str = "2026-01-01"      # 착공 예정일 (ISO yyyy-mm-dd)


class ProjectSettingsDialog(QDialog):
    """프로젝트 설정 모달 — ProjectSettings 값을 읽어 폼을 채우고, 확인 시 되씀.

    on_open_catalog: 트럭 카탈로그 관리 버튼 콜백(없으면 버튼 숨김). 카탈로그
        다이얼로그 자체는 기존 운송 패널 로직을 재사용하므로 본 창은 콜백만
        호출한다.
    """

    def __init__(self, settings: ProjectSettings, on_open_catalog=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("프로젝트 설정")
        self.setMinimumWidth(420)
        self._settings = settings
        self._on_open_catalog = on_open_catalog

        root = QVBoxLayout(self)

        # ── 운송 ─────────────────────────────────────────
        trans_box = QGroupBox("운송 (공통)")
        tf = QFormLayout(trans_box)
        self._distance_spin = QSpinBox()
        self._distance_spin.setRange(1, 9999)
        self._distance_spin.setSuffix(" km")
        tf.addRow("운송 거리 (편도):", self._distance_spin)

        # 운임 방식 — 요금표(기본) / 트레일러별 km단가
        self._cost_mode_combo = QComboBox()
        self._cost_mode_combo.addItem("요금표 (전국특송24시콜)", "freight_table")
        self._cost_mode_combo.addItem("트레일러별 km단가", "per_km")
        tf.addRow("운임 방식:", self._cost_mode_combo)

        # 트럭 종류별 km단가 (per_km 방식에서 사용)
        self._lowbed_per_km_spin = QSpinBox()
        self._lowbed_per_km_spin.setRange(0, 1_000_000)
        self._lowbed_per_km_spin.setSuffix(" 원/km")
        tf.addRow("저상 km단가:", self._lowbed_per_km_spin)

        self._extend_per_km_spin = QSpinBox()
        self._extend_per_km_spin.setRange(0, 1_000_000)
        self._extend_per_km_spin.setSuffix(" 원/km")
        tf.addRow("광폭 km단가:", self._extend_per_km_spin)

        self._aframe_per_km_spin = QSpinBox()
        self._aframe_per_km_spin.setRange(0, 1_000_000)
        self._aframe_per_km_spin.setSuffix(" 원/km")
        tf.addRow("A-frame km단가:", self._aframe_per_km_spin)
        root.addWidget(trans_box)

        # ── 비내력벽 단위중량 ────────────────────────────
        wall_box = QGroupBox("비내력벽 단위중량 (공통)")
        wf = QFormLayout(wall_box)
        self._interior_spin = QDoubleSpinBox()
        self._interior_spin.setRange(0.0, 999.0)
        self._interior_spin.setSuffix(" kg/m²")
        wf.addRow("내부 단위중량:", self._interior_spin)
        self._exterior_spin = QDoubleSpinBox()
        self._exterior_spin.setRange(0.0, 999.0)
        self._exterior_spin.setSuffix(" kg/m²")
        wf.addRow("외부 단위중량:", self._exterior_spin)
        root.addWidget(wall_box)

        # ── 공기(현장 지역·착공일) ───────────────────────
        air_box = QGroupBox("공기 — 현장 지역·착공일 (공통)")
        af = QFormLayout(air_box)
        # 지역 콤보 — 지역그룹 헤더(비활성) + 도시 항목(별표1/별표2 를 itemData 로).
        self._region_combo = QComboBox()
        self._build_region_combo()
        # 지역 변경 → 별표1/별표2 자동 채움.
        self._region_combo.currentIndexChanged.connect(self._on_region_changed)
        af.addRow("현장 지역:", self._region_combo)

        self._bp1_spin = QSpinBox()
        self._bp1_spin.setRange(0, 364)
        self._bp1_spin.setSuffix(" 일/년")
        af.addRow("기초공사 비작업일 [별표1]:", self._bp1_spin)

        self._bp2_spin = QSpinBox()
        self._bp2_spin.setRange(0, 364)
        self._bp2_spin.setSuffix(" 일/년")
        af.addRow("골조공사 비작업일 [별표2]:", self._bp2_spin)

        self._start_date_edit = QDateEdit()
        self._start_date_edit.setCalendarPopup(True)
        self._start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self._start_date_edit.setMinimumDate(QDate(2025, 1, 1))
        self._start_date_edit.setMaximumDate(QDate(2030, 12, 31))
        af.addRow("착공 예정일:", self._start_date_edit)
        root.addWidget(air_box)

        # ── 트럭 카탈로그 (공통 기능) ────────────────────
        if self._on_open_catalog is not None:
            cat_box = QGroupBox("트럭 카탈로그 (공통)")
            cv = QVBoxLayout(cat_box)
            cat_btn = QPushButton("트럭 카탈로그 관리...")
            cat_btn.clicked.connect(self._on_open_catalog)
            cv.addWidget(cat_btn)
            root.addWidget(cat_box)

        # ── 확인/취소 ────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        # 현재 설정값으로 위젯 초기화.
        self._load_from_settings()

    # ── 지역 콤보 구성 ────────────────────────────────────
    def _build_region_combo(self) -> None:
        """지역그룹 헤더(선택 불가) + 도시 항목으로 콤보를 채운다.

        도시 항목의 itemData 에는 (별표1, 별표2) 튜플을 넣어, 선택 시 비작업일
        스핀박스를 자동으로 채울 수 있게 한다. 헤더 항목은 itemData=None 이며
        선택 불가로 비활성화한다.
        """
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QStandardItem
        model = self._region_combo.model()
        last_group = None
        for group, city, bp1, bp2 in CITY_HOLIDAYS:
            if group != last_group:
                # 그룹 헤더 — 선택 불가 항목.
                self._region_combo.addItem(f"── {group} ──", None)
                item = model.item(self._region_combo.count() - 1)
                if isinstance(item, QStandardItem):
                    item.setFlags(item.flags() & ~Qt.ItemIsSelectable
                                  & ~Qt.ItemIsEnabled)
                last_group = group
            self._region_combo.addItem(f"  {city}", (city, bp1, bp2))

    def _on_region_changed(self, _idx: int) -> None:
        """지역 도시 선택 시 별표1/별표2 자동 채움(헤더 선택은 무시)."""
        data = self._region_combo.currentData()
        if not data:
            return
        _city, bp1, bp2 = data
        self._bp1_spin.setValue(int(bp1))
        self._bp2_spin.setValue(int(bp2))

    def _select_city(self, city: str) -> None:
        """도시 라벨로 콤보 항목을 선택한다(없으면 무시)."""
        for i in range(self._region_combo.count()):
            data = self._region_combo.itemData(i)
            if data and data[0] == city:
                self._region_combo.setCurrentIndex(i)
                return

    # ── 설정값 ↔ 위젯 ─────────────────────────────────────
    def _load_from_settings(self) -> None:
        s = self._settings
        self._distance_spin.setValue(int(s.distance_km))
        idx = self._cost_mode_combo.findData(s.cost_mode)
        if idx >= 0:
            self._cost_mode_combo.setCurrentIndex(idx)
        self._lowbed_per_km_spin.setValue(int(s.lowbed_per_km_krw))
        self._extend_per_km_spin.setValue(int(s.extendable_per_km_krw))
        self._aframe_per_km_spin.setValue(int(s.aframe_per_km_krw))
        self._interior_spin.setValue(float(s.wall_interior_kg_m2))
        self._exterior_spin.setValue(float(s.wall_exterior_kg_m2))
        # 지역 선택 → currentIndexChanged 가 별표1/별표2 를 자동 채운 뒤,
        # 저장된 값으로 다시 덮어써 사용자가 수정한 값도 보존한다.
        self._select_city(s.region_city)
        self._bp1_spin.setValue(int(s.nonwork_days_foundation))
        self._bp2_spin.setValue(int(s.nonwork_days_frame))
        d = QDate.fromString(s.start_date, "yyyy-MM-dd")
        if d.isValid():
            self._start_date_edit.setDate(d)
        else:
            self._start_date_edit.setDate(QDate(2026, 1, 1))

    def _on_accept(self) -> None:
        """위젯 값을 ProjectSettings 에 되쓰고 창을 닫는다."""
        s = self._settings
        s.distance_km = float(self._distance_spin.value())
        s.cost_mode = str(self._cost_mode_combo.currentData())
        s.lowbed_per_km_krw = float(self._lowbed_per_km_spin.value())
        s.extendable_per_km_krw = float(self._extend_per_km_spin.value())
        s.aframe_per_km_krw = float(self._aframe_per_km_spin.value())
        s.wall_interior_kg_m2 = float(self._interior_spin.value())
        s.wall_exterior_kg_m2 = float(self._exterior_spin.value())
        data = self._region_combo.currentData()
        if data:
            s.region_city = data[0]
        s.nonwork_days_foundation = int(self._bp1_spin.value())
        s.nonwork_days_frame = int(self._bp2_spin.value())
        s.start_date = self._start_date_edit.date().toString("yyyy-MM-dd")
        self.accept()
