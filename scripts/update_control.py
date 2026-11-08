"""更新任务的协作取消；文件替换事务内部不设置取消点。"""

import time


class UpdateCancelled(Exception):
    """用户关闭窗口，停止更新并保留已提交的数据。"""


def check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelled("更新已取消")


def wait_or_cancel(seconds, cancel_event):
    if cancel_event is None:
        time.sleep(seconds)
    elif cancel_event.wait(seconds):
        raise UpdateCancelled("更新已取消")
