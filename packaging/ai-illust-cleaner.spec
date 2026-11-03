# PyInstaller の設定。tools/build_exe.py から使う（起動の速いフォルダ形式で作り、zip にして配る）
from pathlib import Path

ROOT = Path(SPECPATH).parent
NAME = 'ai-illust-cleaner'

# 使っていない Qt の部品。入れると配布物が大きくなる
QT_EXCLUDES = ['PySide6.' + m for m in (
    'Qt3DAnimation', 'Qt3DCore', 'Qt3DExtras', 'Qt3DInput', 'Qt3DLogic', 'Qt3DRender', 'QtBluetooth',
    'QtCharts', 'QtDataVisualization', 'QtDesigner', 'QtGraphs', 'QtHelp', 'QtLocation', 'QtMultimedia',
    'QtMultimediaWidgets', 'QtNetworkAuth', 'QtNfc', 'QtOpenGL', 'QtOpenGLWidgets', 'QtPdf', 'QtPdfWidgets',
    'QtPositioning', 'QtQml', 'QtQuick', 'QtQuick3D', 'QtQuickControls2', 'QtQuickWidgets', 'QtRemoteObjects',
    'QtScxml', 'QtSensors', 'QtSerialBus', 'QtSerialPort', 'QtSpatialAudio', 'QtSql', 'QtStateMachine',
    'QtSvgWidgets', 'QtTest', 'QtTextToSpeech', 'QtUiTools', 'QtWebChannel', 'QtWebEngineCore',
    'QtWebEngineQuick', 'QtWebEngineWidgets', 'QtWebSockets', 'QtWebView', 'QtXml')]

a = Analysis(
    [str(ROOT / 'packaging' / 'launcher.py')],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / 'gui' / 'assets'), 'gui/assets')],
    excludes=QT_EXCLUDES + ['matplotlib', 'tkinter', 'pytest', 'IPython'],
)
# 依存に付いてくるが使わないファイル（動画の読み込み・ソフトウェアOpenGL・PDF・QML・仮想キーボード・翻訳など）
DROP = ('opencv_videoio_ffmpeg', 'opengl32sw', 'Qt6Pdf', 'Qt6Qml', 'Qt6Quick', 'Qt6VirtualKeyboard',
        'qpdf', 'qtvirtualkeyboard', 'networkinformation', 'plugins/tls', 'translations')


def _keep(entry):
    dest = entry[0].replace('\\', '/')
    return not any(k in dest for k in DROP)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    icon=str(ROOT / 'packaging' / 'icon.ico'),
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
