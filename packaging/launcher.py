"""exe の入口（PyInstaller はパッケージの __main__ を直接入口にできないため）

配布前の動作確認用に、画面を出さずに画像を開いて処理し、画面の画像を書き出して終わる起動方法を持つ:
  ai-illust-cleaner.exe --smoke-test 入力画像 出力.png
失敗したときは、出力.png の横に .log を書いて終了コード 1 で終わる
"""
import sys


def smoke_test(src, out):
    import os
    import traceback
    from pathlib import Path
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    try:
        from PySide6.QtCore import QEventLoop, QTimer
        from gui.__main__ import create_app
        from gui.window import MainWindow
        app = create_app()
        win = MainWindow()
        win.show()

        def wait(ms):
            loop = QEventLoop()
            QTimer.singleShot(ms, loop.quit)
            loop.exec()

        win.open_image(src)
        wait(300)
        for _ in range(600):   # 最大 60 秒
            if win.is_idle() and win.result is not None:
                break
            wait(100)
        else:
            raise TimeoutError('処理が終わりませんでした')
        wait(300)
        win.grab().save(out)
        win.close()
        app.quit()
        return 0
    except Exception:
        Path(out).with_suffix('.log').write_text(traceback.format_exc(), encoding='utf-8')
        return 1


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--smoke-test':
        sys.exit(smoke_test(sys.argv[2], sys.argv[3]))
    from gui.__main__ import main
    main()
