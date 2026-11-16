"""Modern Dark JARVIS HUD Qt StyleSheet (optimized for Windows 11 Segoe UI)."""

JARVIS_DARK_QSS = """
QMainWindow, QWidget#RootWidget {
    background-color: #070B14;
    color: #E2E8F0;
    font-family: "Segoe UI", "Inter", "Noto Sans Tamil", sans-serif;
    font-size: 13px;
}

QFrame#HeaderBar {
    background-color: #0B1220;
    border-bottom: 1px solid #1E293B;
}

QFrame#SidePanel, QFrame#MonitorPanel, QFrame#Card {
    background-color: #0C1424;
    border: 1px solid #1A2740;
    border-radius: 10px;
}

QLabel {
    color: #CBD5E1;
    background: transparent;
}

QLabel#BrandTitle {
    color: #00D4FF;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 2px;
}

QLabel#SubtleLabel {
    color: #94A3B8;
    font-size: 11px;
}

QLabel#SectionHeader {
    color: #38BDF8;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#PillOnline {
    background-color: #064E3B;
    color: #34D399;
    border: 1px solid #10B981;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}

QLabel#PillOffline {
    background-color: #451A03;
    color: #FBBF24;
    border: 1px solid #D97706;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}

QLabel#PillDanger {
    background-color: #450A0A;
    color: #FCA5A5;
    border: 1px solid #EF4444;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}

QLabel#TelemetryBadge {
    background-color: #111C33;
    color: #CBD5E1;
    border: 1px solid #1E3A5F;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
}

QPushButton {
    background-color: #132038;
    color: #E2E8F0;
    border: 1px solid #23385C;
    border-radius: 7px;
    padding: 7px 14px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #1A2D4D;
    border-color: #00D4FF;
}

QPushButton:pressed {
    background-color: #0F1A2E;
}

QPushButton#PrimaryButton {
    background-color: #0284C7;
    color: #FFFFFF;
    border: 1px solid #38BDF8;
}

QPushButton#PrimaryButton:hover {
    background-color: #0369A1;
}

QPushButton#PTTButton {
    background-color: #0F2942;
    color: #38BDF8;
    border: 1px solid #0284C7;
}

QPushButton#PTTButton:pressed {
    background-color: #00D4FF;
    color: #04101C;
}

QPushButton#WarnButton {
    background-color: #3B2506;
    color: #FCD34D;
    border: 1px solid #D97706;
}

QPushButton#WarnButton:hover {
    background-color: #523308;
}

QPushButton#EmergencyButton {
    background-color: #991B1B;
    color: #FFFFFF;
    border: 2px solid #EF4444;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 1px;
}

QPushButton#EmergencyButton:hover {
    background-color: #DC2626;
}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #09101D;
    color: #F1F5F9;
    border: 1px solid #1E3250;
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: #0284C7;
}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid #00D4FF;
}

QComboBox QAbstractItemView {
    background-color: #0D1628;
    color: #F1F5F9;
    border: 1px solid #00D4FF;
    selection-background-color: #0369A1;
}

QTabWidget::pane {
    border: 1px solid #1A2740;
    background-color: #0B1220;
    border-radius: 8px;
}

QTabBar::tab {
    background-color: #09101D;
    color: #94A3B8;
    border: 1px solid #1A2740;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 8px 16px;
    margin-right: 3px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background-color: #112240;
    color: #00D4FF;
    border-color: #0284C7;
}

QTableWidget, QListWidget {
    background-color: #080E1A;
    color: #E2E8F0;
    border: 1px solid #1A2740;
    border-radius: 7px;
    gridline-color: #152238;
}

QHeaderView::section {
    background-color: #101B30;
    color: #7DD3FC;
    border: none;
    border-bottom: 1px solid #1E3250;
    padding: 6px;
    font-weight: 600;
    font-size: 11px;
}

QCheckBox {
    color: #CBD5E1;
    spacing: 8px;
}

QSlider::groove:horizontal {
    height: 6px;
    background: #1A2740;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #00D4FF;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}

QScrollBar:vertical {
    background: #080E1A;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #1E3A5F;
    border-radius: 4px;
    min-height: 24px;
}

QScrollBar:horizontal {
    background: #080E1A;
    height: 8px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal {
    background: #1E3A5F;
    border-radius: 4px;
    min-width: 24px;
}

QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
}
"""
