"""処理中の例外を、画面に出す文にする"""
import traceback

import cv2

OUT_OF_MEMORY = 'メモリが足りず、処理できませんでした。ほかのアプリを閉じるか、小さい画像で試してください。'
_CV_OOM = ('Insufficient memory', 'Failed to allocate')   # OpenCV がメモリ不足のときに出す文


def is_out_of_memory(e):
    return isinstance(e, MemoryError) or (isinstance(e, cv2.error) and any(w in str(e) for w in _CV_OOM))


def summary(e):
    """1行の説明。画像を開けなかったときや、一括処理の失敗の一覧に使う"""
    if is_out_of_memory(e):
        return OUT_OF_MEMORY
    return str(e) or type(e).__name__


def details(e):
    """処理中のエラーのダイアログに出す文。メモリ不足以外は、原因を追えるよう詳細を付ける"""
    if is_out_of_memory(e):
        return OUT_OF_MEMORY
    return ''.join(traceback.format_exception(e))
