"""Windows 平台细节：子进程防闪控制台。

桌面态后端跑在 pywebview（GUI 进程）里：Windows 上 GUI 进程 spawn console
子进程（powershell / taskkill / tasklist / mihomo 等）会新建一个 console
窗口——网络页每次启停内核连闪好几个黑框。CREATE_NO_WINDOW（0x08000000）
让子进程不新建可见 console（stdout/stderr 重定向行为不受影响）。
非 Windows 平台不传该标志位（值为 0，等价缺省）。
"""
import sys

NO_WINDOW_FLAGS = 0x08000000 if sys.platform == "win32" else 0
