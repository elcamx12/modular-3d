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

from PyQt5.QtCore import QDate, Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QPushButton,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
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
    # [2026-06-04] 기본 운임 방식 = 트레일러별 1회 고정비(사용자 확정).
    cost_mode: str = "fixed_per_trip"   # 'freight_table' | 'per_km' | 'fixed_per_trip'
    lowbed_per_km_krw: float = 3500.0   # 저상/초저상 km단가 (per_km 방식)
    extendable_per_km_krw: float = 5000.0  # 광폭(확장형) km단가
    aframe_per_km_krw: float = 5000.0   # A-frame km단가
    # 트레일러별 1회 고정 운송비 (cost_mode='fixed_per_trip' — 거리 무관, 회차당)
    # [2026-06-04] 사용자 확정 기본값: 저상 420만 / 광폭 440만 / A-frame 400만.
    lowbed_fixed_krw: float = 4200000.0
    extendable_fixed_krw: float = 4400000.0
    aframe_fixed_krw: float = 4000000.0
    # 현장 운송 제한 (도로 등급 대체 — 2026-05-26). *_enabled=False → 해당없음(프리패스).
    # [2026-06-04] 기본 GVW 한도 45,000kg(사용자 확정).
    site_limit_gvw_kg: float = 45000.0       # 차체+화물 총중량(GVW) 한도
    site_limit_gvw_enabled: bool = True
    site_limit_width_mm: float = 3500.0      # 화물 폭 한도
    site_limit_width_enabled: bool = True
    site_limit_height_mm: float = 4500.0     # 지면~화물 꼭대기 총높이(차량 포함) 한도
    site_limit_height_enabled: bool = True
    # 비내력벽 단위중량
    wall_interior_kg_m2: float = 30.0   # 내부 단위중량
    wall_exterior_kg_m2: float = 55.0   # 외부 단위중량
    # 공기(현장 지역·착공일)
    # [2026-05-30] 비작업일은 공정표 내부 가동율 모델로 산정 → 별표1/별표2 입력 제거.
    region_city: str = _DEFAULT_CITY    # 현장 지역(도시 라벨)
    start_date: str = "2026-01-01"      # 착공 예정일 (ISO yyyy-mm-dd)


# ─────────────────────────────────────────────────────────
# ProjectSettingsForm — 모든 위젯·로드/적용 로직의 단일 진실 출처.
# [2026-06-01] 다이얼로그와 랜딩 페이지 카드가 같은 폼 위젯 클래스를
# 인스턴스화 → 양쪽 입력/표시 값이 어긋날 가능성 차단.
# ─────────────────────────────────────────────────────────
class ProjectSettingsForm(QWidget):
    """프로젝트 설정 폼.

    Args:
        settings: 읽고 쓸 ProjectSettings 인스턴스.
        layout_mode: "vertical" (다이얼로그용 세로 스택) 또는
                     "two-column" (랜딩용 가로 2 컬럼).
        on_open_catalog: 트럭 카탈로그 관리 버튼 콜백. None 이면 버튼 숨김.
    """

    def __init__(self, settings: 'ProjectSettings',
                 layout_mode: str = "vertical",
                 on_open_catalog=None,
                 parent=None):
        super().__init__(parent)
        self._settings = settings
        self._on_open_catalog = on_open_catalog
        self._layout_mode = layout_mode

        # 위젯 인스턴스 생성 — 한 번만, 두 레이아웃 모드에서 공유.
        self._build_widgets()
        # 레이아웃 배치 (모드별).
        if layout_mode == "two-column":
            self._lay_two_column()
        else:
            self._lay_vertical()
        # 초기값 채움.
        self.load_from_settings()

    # ── 위젯 생성 (1 회) ─────────────────────────────────
    def _build_widgets(self) -> None:
        # 운송
        self._distance_spin = QSpinBox()
        self._distance_spin.setRange(1, 9999)
        self._distance_spin.setSuffix(" km")
        self._cost_mode_combo = QComboBox()
        self._cost_mode_combo.addItem("요금표 (전국특송24시콜)", "freight_table")
        self._cost_mode_combo.addItem("트레일러별 km단가", "per_km")
        self._cost_mode_combo.addItem("트레일러별 1회 고정비", "fixed_per_trip")
        self._cost_mode_combo.currentIndexChanged.connect(self._on_cost_mode_changed)
        # km 단가 (km mode)
        self._lowbed_per_km_spin = self._krw_spin(" 원/km", 1_000_000)
        self._extend_per_km_spin = self._krw_spin(" 원/km", 1_000_000)
        self._aframe_per_km_spin = self._krw_spin(" 원/km", 1_000_000)
        # 1회 고정비 (fixed_per_trip mode)
        self._lowbed_fixed_spin = self._krw_spin(" 원", 100_000_000, step=10_000)
        self._extend_fixed_spin = self._krw_spin(" 원", 100_000_000, step=10_000)
        self._aframe_fixed_spin = self._krw_spin(" 원", 100_000_000, step=10_000)

        # 현장 운송 제한 — 위젯 생성만 (행 빌드는 레이아웃 시).
        self._site_gvw_spin    = self._limit_spin(" kg", 1_000_000)
        self._site_gvw_none    = QCheckBox("해당없음")
        self._site_gvw_none.toggled.connect(
            lambda chk: self._site_gvw_spin.setEnabled(not chk))
        self._site_width_spin  = self._limit_spin(" mm", 100_000)
        self._site_width_none  = QCheckBox("해당없음")
        self._site_width_none.toggled.connect(
            lambda chk: self._site_width_spin.setEnabled(not chk))
        self._site_height_spin = self._limit_spin(" mm", 100_000)
        self._site_height_none = QCheckBox("해당없음")
        self._site_height_none.toggled.connect(
            lambda chk: self._site_height_spin.setEnabled(not chk))

        # 비내력벽 단위중량
        self._interior_spin = QDoubleSpinBox()
        self._interior_spin.setRange(0.0, 999.0)
        self._interior_spin.setSuffix(" kg/m²")
        self._exterior_spin = QDoubleSpinBox()
        self._exterior_spin.setRange(0.0, 999.0)
        self._exterior_spin.setSuffix(" kg/m²")

        # 공기 — 지역·착공일
        self._region_combo = QComboBox()
        self._build_region_combo()
        self._region_combo.currentIndexChanged.connect(self._on_region_changed)
        self._start_date_edit = QDateEdit()
        self._start_date_edit.setCalendarPopup(True)
        self._start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self._start_date_edit.setMinimumDate(QDate(2025, 1, 1))
        self._start_date_edit.setMaximumDate(QDate(2030, 12, 31))

    @staticmethod
    def _krw_spin(suffix: str, maximum: int, step: int = 1) -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, maximum)
        s.setSuffix(suffix)
        s.setSingleStep(step)
        s.setGroupSeparatorShown(True)
        return s

    @staticmethod
    def _limit_spin(suffix: str, maximum: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(1, maximum)
        s.setSuffix(suffix)
        return s

    # ── 그룹박스 빌더 ─────────────────────────────────────
    def _build_air_box(self) -> QGroupBox:
        box = QGroupBox("공기 — 현장 지역·착공일")
        f = QFormLayout(box)
        f.addRow("현장 지역:", self._region_combo)
        f.addRow("착공 예정일:", self._start_date_edit)
        return box

    def _build_transport_box(self, include_bulk_btn: bool = True) -> QGroupBox:
        box = QGroupBox("운송 (공통)")
        f = QFormLayout(box)
        f.addRow("운송 거리 (편도):", self._distance_spin)
        f.addRow("운임 방식:", self._cost_mode_combo)
        f.addRow("저상 km단가:", self._lowbed_per_km_spin)
        f.addRow("광폭 km단가:", self._extend_per_km_spin)
        f.addRow("A-frame km단가:", self._aframe_per_km_spin)
        f.addRow("저상 1회 고정비:", self._lowbed_fixed_spin)
        f.addRow("광폭 1회 고정비:", self._extend_fixed_spin)
        f.addRow("A-frame 1회 고정비:", self._aframe_fixed_spin)
        if include_bulk_btn:
            bulk_btn = QPushButton("1회 고정비 일괄 적용 (저상값 → 전체)")
            bulk_btn.clicked.connect(self._apply_fixed_bulk)
            f.addRow("", bulk_btn)
        return box

    def _build_site_box(self) -> QGroupBox:
        """현장 운송 제한 그룹.

        [2026-06-01 v4] QFormLayout 이 stylesheet 의 QSpinBox min-height 와
        충돌해 라벨이 spin 위에 겹쳐 그려지는 문제 발생 → 직접 QGridLayout
        으로 짜서 행 높이를 명시 통제. 각 행은 라벨/spin/체크박스가 한 줄에.
        """
        from PyQt5.QtWidgets import QLabel as _QLabel, QGridLayout as _QGrid
        box = QGroupBox("현장 운송 제한 (공통)")
        g = _QGrid(box)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        g.setContentsMargins(8, 12, 8, 8)
        if self._layout_mode == "two-column":
            labels = ("총중량(GVW)", "폭", "높이")
            lbl_min = 70
        else:
            labels = ("총중량 한도(차체+화물)", "폭 한도", "높이 한도(차량 포함)")
            lbl_min = 150

        items = (
            (self._site_gvw_spin,    self._site_gvw_none),
            (self._site_width_spin,  self._site_width_none),
            (self._site_height_spin, self._site_height_none),
        )
        for r, ((spin, none_cb), label_text) in enumerate(zip(items, labels)):
            lbl = _QLabel(label_text)
            lbl.setMinimumWidth(lbl_min)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            spin.setMinimumWidth(70)
            g.addWidget(lbl,    r, 0)
            g.addWidget(spin,   r, 1)
            g.addWidget(none_cb, r, 2)
        g.setColumnStretch(1, 1)
        return box

    def _build_wall_box(self) -> QGroupBox:
        box = QGroupBox("비내력벽 단위중량 (공통)")
        f = QFormLayout(box)
        f.addRow("내부 단위중량:", self._interior_spin)
        f.addRow("외부 단위중량:", self._exterior_spin)
        return box

    def _build_catalog_box(self) -> QGroupBox:
        box = QGroupBox("트럭 카탈로그 (공통)")
        v = QVBoxLayout(box)
        cat_btn = QPushButton("트럭 카탈로그 관리...")
        cat_btn.clicked.connect(self._on_open_catalog)
        v.addWidget(cat_btn)
        return box

    # ── 레이아웃 모드 ─────────────────────────────────────
    def _lay_vertical(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._build_transport_box(include_bulk_btn=True))
        root.addWidget(self._build_site_box())
        root.addWidget(self._build_wall_box())
        root.addWidget(self._build_air_box())
        if self._on_open_catalog is not None:
            root.addWidget(self._build_catalog_box())

    def _lay_two_column(self) -> None:
        """좌(운송) / 우(공기·현장제한·벽 세로 스택).

        [2026-06-01] 우측 컬럼 stretch 4 → 5 — 현장 제한 박스의 spin + 체크박스
        가 잘리지 않게 가로 폭 확보.
        """
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)
        outer.addWidget(self._build_transport_box(include_bulk_btn=False), stretch=5)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_air_box())
        right.addWidget(self._build_site_box())
        right.addWidget(self._build_wall_box())
        if self._on_open_catalog is not None:
            right.addWidget(self._build_catalog_box())
        right.addStretch(1)
        right_host = QWidget()
        right_host.setLayout(right)
        outer.addWidget(right_host, stretch=5)

    # ── 지역 콤보 ────────────────────────────────────────
    def _build_region_combo(self) -> None:
        from PyQt5.QtCore import Qt as _Qt
        from PyQt5.QtGui import QStandardItem
        model = self._region_combo.model()
        last_group = None
        for group, city, bp1, bp2 in CITY_HOLIDAYS:
            if group != last_group:
                self._region_combo.addItem(f"── {group} ──", None)
                item = model.item(self._region_combo.count() - 1)
                if isinstance(item, QStandardItem):
                    item.setFlags(item.flags() & ~_Qt.ItemIsSelectable
                                  & ~_Qt.ItemIsEnabled)
                last_group = group
            self._region_combo.addItem(f"  {city}", (city, bp1, bp2))

    def _on_region_changed(self, _idx: int) -> None:
        return  # [2026-05-30] 별표1/별표2 자동입력 제거

    def _select_city(self, city: str) -> None:
        for i in range(self._region_combo.count()):
            data = self._region_combo.itemData(i)
            if data and data[0] == city:
                self._region_combo.setCurrentIndex(i)
                return

    def _on_cost_mode_changed(self) -> None:
        mode = str(self._cost_mode_combo.currentData() or "")
        self._distance_spin.setEnabled(mode != "fixed_per_trip")

    def _apply_fixed_bulk(self) -> None:
        v = self._lowbed_fixed_spin.value()
        self._extend_fixed_spin.setValue(v)
        self._aframe_fixed_spin.setValue(v)

    # ── 설정값 ↔ 위젯 ────────────────────────────────────
    def load_from_settings(self) -> None:
        s = self._settings
        self._distance_spin.setValue(int(s.distance_km))
        idx = self._cost_mode_combo.findData(s.cost_mode)
        if idx >= 0:
            self._cost_mode_combo.setCurrentIndex(idx)
        self._on_cost_mode_changed()
        self._lowbed_per_km_spin.setValue(int(s.lowbed_per_km_krw))
        self._extend_per_km_spin.setValue(int(s.extendable_per_km_krw))
        self._aframe_per_km_spin.setValue(int(s.aframe_per_km_krw))
        self._lowbed_fixed_spin.setValue(int(s.lowbed_fixed_krw))
        self._extend_fixed_spin.setValue(int(s.extendable_fixed_krw))
        self._aframe_fixed_spin.setValue(int(s.aframe_fixed_krw))
        self._site_gvw_spin.setValue(int(s.site_limit_gvw_kg))
        self._site_gvw_none.setChecked(not s.site_limit_gvw_enabled)
        self._site_gvw_spin.setEnabled(s.site_limit_gvw_enabled)
        self._site_width_spin.setValue(int(s.site_limit_width_mm))
        self._site_width_none.setChecked(not s.site_limit_width_enabled)
        self._site_width_spin.setEnabled(s.site_limit_width_enabled)
        self._site_height_spin.setValue(int(s.site_limit_height_mm))
        self._site_height_none.setChecked(not s.site_limit_height_enabled)
        self._site_height_spin.setEnabled(s.site_limit_height_enabled)
        self._interior_spin.setValue(float(s.wall_interior_kg_m2))
        self._exterior_spin.setValue(float(s.wall_exterior_kg_m2))
        self._select_city(s.region_city)
        d = QDate.fromString(s.start_date, "yyyy-MM-dd")
        if d.isValid():
            self._start_date_edit.setDate(d)
        else:
            self._start_date_edit.setDate(QDate(2026, 1, 1))

    def apply_to_settings(self) -> None:
        s = self._settings
        s.distance_km = float(self._distance_spin.value())
        s.cost_mode = str(self._cost_mode_combo.currentData())
        s.lowbed_per_km_krw = float(self._lowbed_per_km_spin.value())
        s.extendable_per_km_krw = float(self._extend_per_km_spin.value())
        s.aframe_per_km_krw = float(self._aframe_per_km_spin.value())
        s.lowbed_fixed_krw = float(self._lowbed_fixed_spin.value())
        s.extendable_fixed_krw = float(self._extend_fixed_spin.value())
        s.aframe_fixed_krw = float(self._aframe_fixed_spin.value())
        s.site_limit_gvw_kg = float(self._site_gvw_spin.value())
        s.site_limit_gvw_enabled = not self._site_gvw_none.isChecked()
        s.site_limit_width_mm = float(self._site_width_spin.value())
        s.site_limit_width_enabled = not self._site_width_none.isChecked()
        s.site_limit_height_mm = float(self._site_height_spin.value())
        s.site_limit_height_enabled = not self._site_height_none.isChecked()
        s.wall_interior_kg_m2 = float(self._interior_spin.value())
        s.wall_exterior_kg_m2 = float(self._exterior_spin.value())
        data = self._region_combo.currentData()
        if data:
            s.region_city = data[0]
        s.start_date = self._start_date_edit.date().toString("yyyy-MM-dd")


class ProjectSettingsDialog(QDialog):
    """프로젝트 설정 모달 — ProjectSettingsForm 을 임베드 + OK/Cancel 만 추가.

    [2026-06-01] 폼 위젯 정의가 ProjectSettingsForm 하나로 일원화됨.
    랜딩 카드와 본 다이얼로그가 같은 위젯 클래스 인스턴스를 만들기 때문에
    어떤 식으로 입력하든 ProjectSettings 에 적용되는 값이 항상 일치한다.
    """

    def __init__(self, settings: ProjectSettings, on_open_catalog=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("프로젝트 설정")
        self.setMinimumWidth(420)
        self._settings = settings
        self._form = ProjectSettingsForm(
            settings, layout_mode="vertical",
            on_open_catalog=on_open_catalog,
        )
        root = QVBoxLayout(self)
        root.addWidget(self._form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _on_accept(self) -> None:
        self._form.apply_to_settings()
        self.accept()


# ─────────────────────────────────────────────────────────
# 옛 코드 — 위 ProjectSettingsForm 으로 대체됨. 아래 본문은 더이상 사용하지
# 않지만, 외부 참조 안정성을 위해 클래스 정의는 위에서 이미 교체됐다.
# ─────────────────────────────────────────────────────────
class _LegacyProjectSettingsDialog:
    def __init__(self, settings: ProjectSettings, on_open_catalog=None, parent=None):
        # 이 클래스는 더이상 사용되지 않음 — ProjectSettingsDialog 위 본문 참고.
        raise NotImplementedError("Use ProjectSettingsDialog above")
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
        self._cost_mode_combo.addItem("트레일러별 1회 고정비", "fixed_per_trip")
        tf.addRow("운임 방식:", self._cost_mode_combo)
        # 운임 방식이 거리와 무관한 'fixed_per_trip' 일 때 거리 입력 칸 비활성.
        self._cost_mode_combo.currentIndexChanged.connect(
            self._on_cost_mode_changed)

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

        # 트레일러별 1회 고정비 (운임 방식 '트레일러별 1회 고정비' 에서 사용)
        def _mk_fixed():
            s = QSpinBox()
            s.setRange(0, 100_000_000)
            s.setSingleStep(10_000)
            s.setSuffix(" 원")
            s.setGroupSeparatorShown(True)
            return s

        self._lowbed_fixed_spin = _mk_fixed()
        tf.addRow("저상 1회 고정비:", self._lowbed_fixed_spin)
        self._extend_fixed_spin = _mk_fixed()
        tf.addRow("광폭 1회 고정비:", self._extend_fixed_spin)
        self._aframe_fixed_spin = _mk_fixed()
        tf.addRow("A-frame 1회 고정비:", self._aframe_fixed_spin)
        bulk_btn = QPushButton("1회 고정비 일괄 적용 (저상값 → 전체)")
        bulk_btn.clicked.connect(self._apply_fixed_bulk)
        tf.addRow("", bulk_btn)
        root.addWidget(trans_box)

        # ── 현장 운송 제한 ───────────────────────────────
        # 도로 등급 선택을 대체. 무게=차체+화물(GVW), 폭, 높이(차량 포함 총높이).
        # "해당없음" 체크 시 그 항목은 제한하지 않는다(프리패스).
        site_box = QGroupBox("현장 운송 제한 (공통)")
        sf = QFormLayout(site_box)

        def _limit_row(spin_attr, none_attr, suffix, maximum):
            spin = QSpinBox()
            spin.setRange(1, maximum)
            spin.setSuffix(suffix)
            none_cb = QCheckBox("해당없음")
            none_cb.toggled.connect(lambda checked: spin.setEnabled(not checked))
            setattr(self, spin_attr, spin)
            setattr(self, none_attr, none_cb)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(spin, stretch=1)
            row.addWidget(none_cb)
            holder = QWidget()
            holder.setLayout(row)
            return holder

        sf.addRow("총중량 한도(차체+화물):",
                  _limit_row("_site_gvw_spin", "_site_gvw_none", " kg", 1_000_000))
        sf.addRow("폭 한도:",
                  _limit_row("_site_width_spin", "_site_width_none", " mm", 100_000))
        sf.addRow("높이 한도(차량 포함):",
                  _limit_row("_site_height_spin", "_site_height_none", " mm", 100_000))
        root.addWidget(site_box)

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

        # [2026-05-30] 기초/골조 비작업일 입력 제거 — 공정표 가동율 모델로 일괄 계산.
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
        """지역 도시 선택 — [2026-05-30] 별표1/별표2 자동입력 제거 (가동율 모델로 대체)."""
        return

    def _select_city(self, city: str) -> None:
        """도시 라벨로 콤보 항목을 선택한다(없으면 무시)."""
        for i in range(self._region_combo.count()):
            data = self._region_combo.itemData(i)
            if data and data[0] == city:
                self._region_combo.setCurrentIndex(i)
                return

    # ── 운임 방식 → 거리 입력 활성/비활성 ───────────────────
    def _on_cost_mode_changed(self) -> None:
        """운임 방식이 'fixed_per_trip' (회차당 고정비) 이면 거리 입력 무의미.
        편도거리 SpinBox 를 비활성·회색으로 만들어 사용자 혼란 차단."""
        mode = str(self._cost_mode_combo.currentData() or "")
        self._distance_spin.setEnabled(mode != "fixed_per_trip")

    # ── 설정값 ↔ 위젯 ─────────────────────────────────────
    def _load_from_settings(self) -> None:
        s = self._settings
        self._distance_spin.setValue(int(s.distance_km))
        idx = self._cost_mode_combo.findData(s.cost_mode)
        if idx >= 0:
            self._cost_mode_combo.setCurrentIndex(idx)
        # 초기 로드 후 활성 상태 동기화.
        self._on_cost_mode_changed()
        self._lowbed_per_km_spin.setValue(int(s.lowbed_per_km_krw))
        self._extend_per_km_spin.setValue(int(s.extendable_per_km_krw))
        self._aframe_per_km_spin.setValue(int(s.aframe_per_km_krw))
        self._lowbed_fixed_spin.setValue(int(s.lowbed_fixed_krw))
        self._extend_fixed_spin.setValue(int(s.extendable_fixed_krw))
        self._aframe_fixed_spin.setValue(int(s.aframe_fixed_krw))
        # 현장 제한
        self._site_gvw_spin.setValue(int(s.site_limit_gvw_kg))
        self._site_gvw_none.setChecked(not s.site_limit_gvw_enabled)
        self._site_gvw_spin.setEnabled(s.site_limit_gvw_enabled)
        self._site_width_spin.setValue(int(s.site_limit_width_mm))
        self._site_width_none.setChecked(not s.site_limit_width_enabled)
        self._site_width_spin.setEnabled(s.site_limit_width_enabled)
        self._site_height_spin.setValue(int(s.site_limit_height_mm))
        self._site_height_none.setChecked(not s.site_limit_height_enabled)
        self._site_height_spin.setEnabled(s.site_limit_height_enabled)
        self._interior_spin.setValue(float(s.wall_interior_kg_m2))
        self._exterior_spin.setValue(float(s.wall_exterior_kg_m2))
        # [2026-05-30] 비작업일 자동입력 제거 → 지역 선택만 복원.
        self._select_city(s.region_city)
        d = QDate.fromString(s.start_date, "yyyy-MM-dd")
        if d.isValid():
            self._start_date_edit.setDate(d)
        else:
            self._start_date_edit.setDate(QDate(2026, 1, 1))

    def _apply_fixed_bulk(self) -> None:
        """1회 고정비 일괄 적용 — 저상 입력값을 광폭·A-frame 에도 복사."""
        v = self._lowbed_fixed_spin.value()
        self._extend_fixed_spin.setValue(v)
        self._aframe_fixed_spin.setValue(v)

    def _on_accept(self) -> None:
        """위젯 값을 ProjectSettings 에 되쓰고 창을 닫는다."""
        s = self._settings
        s.distance_km = float(self._distance_spin.value())
        s.cost_mode = str(self._cost_mode_combo.currentData())
        s.lowbed_per_km_krw = float(self._lowbed_per_km_spin.value())
        s.extendable_per_km_krw = float(self._extend_per_km_spin.value())
        s.aframe_per_km_krw = float(self._aframe_per_km_spin.value())
        s.lowbed_fixed_krw = float(self._lowbed_fixed_spin.value())
        s.extendable_fixed_krw = float(self._extend_fixed_spin.value())
        s.aframe_fixed_krw = float(self._aframe_fixed_spin.value())
        # 현장 제한
        s.site_limit_gvw_kg = float(self._site_gvw_spin.value())
        s.site_limit_gvw_enabled = not self._site_gvw_none.isChecked()
        s.site_limit_width_mm = float(self._site_width_spin.value())
        s.site_limit_width_enabled = not self._site_width_none.isChecked()
        s.site_limit_height_mm = float(self._site_height_spin.value())
        s.site_limit_height_enabled = not self._site_height_none.isChecked()
        s.wall_interior_kg_m2 = float(self._interior_spin.value())
        s.wall_exterior_kg_m2 = float(self._exterior_spin.value())
        data = self._region_combo.currentData()
        if data:
            s.region_city = data[0]
        # [2026-05-30] 비작업일 필드 제거 — 가동율 모델로 대체.
        s.start_date = self._start_date_edit.date().toString("yyyy-MM-dd")
        self.accept()
