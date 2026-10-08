"""旧定时任务入口：仍执行一次扫描，共用同一实现。"""

from seu_monitor.core.runner import run_all

if __name__ == "__main__":
    run_all()
