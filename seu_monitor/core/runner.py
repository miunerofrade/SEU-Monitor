"""教务处单次扫描：先归档，再推送；失败的通知下次重试。"""

from __future__ import annotations
import logging
from pathlib import Path
from seu_monitor.adapters.wp_news import WpNewsAdapter
from seu_monitor.sources.jwc import site_config
from .attachments import download_attachments
from .healthcheck import check_vpn_verbose
from .http import new_session
from .notify import FeishuNotifier
from .settings import Settings
from .snapshot import SnapshotStore
from .state import StateStore
from .content_dedup import ContentIndex
from .column_archive import archive_lock

logger = logging.getLogger(__name__)


def run_all(settings: Settings | None = None) -> int:
    settings = settings or Settings.from_env_and_yaml()
    settings.validate()
    with archive_lock(settings.snapshot_root):
        return _run_all(settings)


def _run_all(settings: Settings) -> int:
    state = StateStore(settings.store_root)
    snapshot = SnapshotStore(settings.snapshot_root)
    migrated = snapshot.pack_legacy() if not settings.dry_run else 0
    if migrated:
        logger.info("已迁移 %d 条通知到栏目 ZIP", migrated)
    content_index = ContentIndex.load(settings.snapshot_root, state)
    notifier = FeishuNotifier(settings.feishu_webhook)
    proxies = settings.resolve_proxies_dict()
    if settings.vpn_enabled and not run_check_vpn(settings):
        logger.warning("校园 VPN 不可用，本轮改为直连抓取；失败的栏目或通知下次重试")
        proxies = {}
    total = 0
    with new_session(settings.request_timeout, proxy_override=proxies) as session:
        adapter = WpNewsAdapter(site_config(), session=session)
        for column in site_config()["columns"]:
            seen = state.load(column["name"])
            try:
                notices = adapter.fetch_list(column["list_url"])
            except Exception:
                logger.warning(
                    "%s 列表抓取失败，下次重试", column["name"], exc_info=True
                )
                continue
            for notice in reversed(notices):
                if notice.id in seen:
                    continue
                notice.column_id = column["id"]
                try:
                    detail = adapter.fetch_detail(notice)
                    if settings.dry_run:
                        logger.info("试运行：[%s] %s", column["name"], notice.title)
                        continue
                    with snapshot.staging(notice, detail.attachments) as (directory, previous):
                        attachments = download_attachments(
                            detail.attachments, directory / "attachments", session=session,
                            previous=previous,
                        )
                        snapshot.save(notice, detail, attachments)
                        if any(a.error for a in attachments):
                            snapshot.commit(notice)
                            logger.warning("%s 有附件下载失败，保留重试", notice.title)
                            continue
                        metadata = snapshot.metadata(notice)
                        try:
                            duplicate = content_index.find(metadata, detail.text)
                        except ValueError:
                            duplicate = None
                        if duplicate is not None and duplicate != snapshot.reference(notice):
                            state.mark_seen(column["name"], notice.id)
                            seen.add(notice.id)
                            logger.info("跳过重复内容：%s；保留归档 %s", notice.title, duplicate)
                            continue
                        snapshot.commit(notice)
                        delivered = not settings.feishu_webhook or notifier.send(
                            column["name"],
                            notice.title,
                            notice.date,
                            notice.url,
                            detail.text,
                        )
                        if delivered:
                            state.mark_seen(column["name"], notice.id)
                            seen.add(notice.id)
                            try:
                                content_index.add(metadata, snapshot.reference(notice), detail.text)
                            except ValueError:
                                pass  # 空通知仍允许推送，但不作为内容去重依据。
                            total += 1
                except Exception:
                    logger.warning("%s 处理失败，下次重试", notice.title, exc_info=True)
    logger.info("扫描完成，归档 %d 条新通知", total)
    return total


def run_check_vpn(settings: Settings | None = None) -> bool:
    settings = settings or Settings.from_env_and_yaml()
    ok, message = check_vpn_verbose(
        settings.vpn_check_url or "https://cvs.seu.edu.cn",
        settings.effective_vpn_proxy,
        settings.request_timeout,
    )
    print(f"VPN：{'正常' if ok else '不可用'}（{message}）")
    return ok


def run_check(settings: Settings | None = None) -> bool:
    settings = settings or Settings.from_env_and_yaml()
    settings.validate()
    print(f"通知保存：{Path(settings.snapshot_root).resolve()}")
    if settings.vpn_enabled and not run_check_vpn(settings):
        return False
    with new_session(
        settings.request_timeout, proxy_override=settings.resolve_proxies_dict()
    ) as session:
        try:
            response = session.get(
                site_config()["columns"][0]["list_url"],
                timeout=settings.request_timeout,
            )
            response.raise_for_status()
            print("教务处：可连接")
            return True
        except Exception:
            print("教务处：连接失败")
            return False
