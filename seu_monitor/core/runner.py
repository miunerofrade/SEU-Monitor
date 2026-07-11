"""核心调度器：加载配置 → 健康检查 → 遍历站点/栏目 → 去重 → 快照 → 推送。

支持：
- auth: public / auth: vpn / auth: mixed 站点和栏目
- 顶级 vpn 配置（enabled / required / fail_fast / proxy / healthcheck_url）
- --dry-run 模式
- --site 单站点运行
- --check / --check-vpn 健康检查
- 健康检查结果缓存（每次运行最多一次实际检查）
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Type

import yaml

from seu_monitor.adapters.base import SiteAdapter
from seu_monitor.adapters.wp_news import WpNewsAdapter
from seu_monitor.core.attachments import download_attachments
from seu_monitor.core.healthcheck import check_vpn, check_vpn_verbose
from seu_monitor.core.http import new_session
from seu_monitor.core.notify import FeishuNotifier
from seu_monitor.core.settings import Settings
from seu_monitor.core.snapshot import SnapshotStore, _resolve_root
from seu_monitor.core.state import StateStore

logger = logging.getLogger(__name__)

ADAPTER_REGISTRY: Dict[str, Type[SiteAdapter]] = {
    "wp_news": WpNewsAdapter,
}

# 全局健康检查缓存（每次运行最多一次实际检查）
_healthcheck_cache: Optional[bool] = None
_alert_sent_for_run: bool = False  # 确保每次运行只发一次 VPN 告警


def _needs_vpn(auth: str) -> bool:
    """判断一个 auth 值是否需要 VPN。"""
    return auth in ("vpn", "mixed")


def _get_column_auth(col: dict, site_auth: str) -> str:
    """返回栏目的最终 auth 值。"""
    col_auth = col.get("auth", site_auth)
    return col_auth or site_auth


def resolve_proxy(
    site_cfg: dict,
    settings: Settings,
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """解析站点级代理，返回 (proxy_dict, proxy_url_for_healthcheck)。"""
    site_proxy = site_cfg.get("proxy")
    if site_proxy:
        return {"http": site_proxy, "https": site_proxy}, site_proxy
    proxies = settings.resolve_proxies_dict()
    if proxies:
        return proxies, proxies.get("https") or proxies.get("http")
    return None, None


def prepare_column_session(
    col_auth: str,
    proxy_dict: Optional[Dict[str, str]],
    default_session,
    settings: Settings,
) -> Tuple:
    """为栏目准备 HTTP session。

    Returns:
        (session, needs_vpn_check)
    """
    needs_check = False
    if col_auth in ("vpn", "mixed"):
        needs_check = True

    # 如果栏目需要 VPN 且有独立代理，创建专用 session
    if needs_check and proxy_dict:
        col_session = new_session(
            timeout=settings.request_timeout,
            proxy_override=proxy_dict,
        )
    else:
        col_session = default_session

    return col_session, needs_check


def check_and_alert_vpn(settings: Settings, notifier: FeishuNotifier) -> bool:
    """执行 VPN 健康检查，失败时发送告警。

    Returns:
        True 可用，False 不可用。
    """
    global _healthcheck_cache, _alert_sent_for_run

    if _healthcheck_cache is not None:
        return _healthcheck_cache

    check_url = settings.vpn_check_url
    proxy_url = settings.effective_vpn_proxy

    if not check_url:
        _healthcheck_cache = True
        return True

    ok, msg = check_vpn_verbose(
        check_url=check_url,
        proxy_url=proxy_url,
        timeout=settings.request_timeout,
    )

    _healthcheck_cache = ok

    if not ok and not _alert_sent_for_run:
        _alert_sent_for_run = True
        notifier.send_alert(
            message=(
                f"VPN 代理不可用，可能需要 VNC 手动重新登录 aTrust。\n"
                f"检查 URL: {check_url}\n"
                f"代理: {proxy_url or '(无)'}\n"
                f"详情: {msg}"
            ),
            title="⚠️ SEU-Monitor: VPN 掉线",
        )

    return ok


def run_all(settings: Optional[Settings] = None) -> int:
    """执行一次完整监控扫描。

    Args:
        settings: 运行时配置。

    Returns:
        本次新增的公告总数。
    """
    global _healthcheck_cache, _alert_sent_for_run
    _healthcheck_cache = None
    _alert_sent_for_run = False

    if settings is None:
        settings = Settings.from_env_and_yaml()

    # 校验关键路径配置
    settings.validate()

    # 加载 YAML 配置
    try:
        sites, raw_yaml = load_config(settings.config_path)
    except FileNotFoundError:
        print(f"❌ 配置文件未找到: {settings.config_path}")
        return 0
    except yaml.YAMLError as e:
        print(f"❌ 配置文件格式错误: {e}")
        return 0

    # 从 YAML 顶层覆盖 settings（环境变量优先已在 from_env_and_yaml 处理）
    yaml_store_root = raw_yaml.get("store_root")
    yaml_snapshot_root = raw_yaml.get("snapshot_root")
    if yaml_store_root and not os.environ.get("STORE_ROOT"):
        settings.store_root = yaml_store_root
    if yaml_snapshot_root and not os.environ.get("SNAPSHOT_ROOT"):
        settings.snapshot_root = yaml_snapshot_root

    beijing_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    print(f"\U0001f680 北京时间 {beijing_time} 开始扫描...")
    if settings.dry_run:
        print("   (dry-run 模式：不会发送飞书、不会标记已见、不会下载附件)")

    state = StateStore(store_root=settings.store_root)
    notifier = FeishuNotifier(webhook_url=settings.feishu_webhook)

    http_session = new_session(timeout=settings.request_timeout)

    # ---- 全局 VPN 健康检查（当 VPN_REQUIRED=true 时） ----
    vpn_ok = True
    if settings.vpn_enabled and settings.vpn_required:
        vpn_ok = check_and_alert_vpn(settings, notifier)
        if not vpn_ok and settings.vpn_fail_fast:
            print("  ❌ VPN 不可用且 VPN_FAIL_FAST=true，本次任务终止。")
            print("     请 VNC 手动登录 aTrust 后重试。")
            return 0

    total_new = 0
    for site_cfg in sites:
        site_id = site_cfg.get("id", "")
        if settings.site_filter and site_id != settings.site_filter:
            continue

        print(f"\n\U0001f4cc [{site_id}] {site_cfg.get('name', '')}")
        site_auth = site_cfg.get("auth", "public")
        proxy_dict, proxy_url = resolve_proxy(site_cfg, settings)

        # ---- 站点级 VPN 检查（非全局模式时按需检查） ----
        site_vpn_ok = True
        if (
            settings.vpn_enabled
            and not settings.vpn_required
            and _needs_vpn(site_auth)
        ):
            # 没有全局检查过，按需检查
            site_vpn_ok = check_and_alert_vpn(settings, notifier)

        if not site_vpn_ok:
            print(f"  ❌ [{site_id}] VPN 不可用，跳过")
            continue

        # ---- 创建 adapter ----
        adapter_cls = ADAPTER_REGISTRY.get(site_cfg.get("adapter", "wp_news"))
        if adapter_cls is None:
            logger.error("未知 adapter: %s", site_cfg.get("adapter"))
            continue

        try:
            if proxy_dict and proxy_dict != settings.resolve_proxies_dict():
                site_session = new_session(
                    timeout=settings.request_timeout,
                    proxy_override=proxy_dict,
                )
            else:
                site_session = http_session
            adapter = adapter_cls(site_cfg, session=site_session)
        except Exception as e:
            logger.error("创建 adapter 失败 (%s): %s", site_id, e)
            continue

        snapshot_enabled = bool(settings.snapshot_root) and not settings.dry_run
        snapshot = SnapshotStore(snapshot_root=settings.snapshot_root) if snapshot_enabled else None

        columns = site_cfg.get("columns", [])
        for col in columns:
            col_id = col.get("id", "")
            col_name = col.get("name", col_id)
            col_auth = _get_column_auth(col, site_auth)
            list_url = col.get("list_url", "")

            if not list_url:
                logger.warning("栏目 %s 缺少 list_url，跳过", col_name)
                continue

            # ---- 栏目级 VPN 判断 ----
            col_vpn_ok = True
            if site_vpn_ok and _needs_vpn(col_auth):
                # 如果站点/全局没检查过，这里做按需检查
                if not settings.vpn_required:
                    col_vpn_ok = check_and_alert_vpn(settings, notifier)
                # 全局 fail_fast 已在前面处理，这里仅跳过该栏目
                elif settings.vpn_enabled and not vpn_ok:
                    col_vpn_ok = False

            if not col_vpn_ok:
                print(f"  ⏭️ [{col_name}] 需要 VPN 但不可用，跳过")
                continue

            # ---- 准备栏目级 session ----
            col_session, _ = prepare_column_session(
                col_auth, proxy_dict, site_session, settings
            )

            # 1. 读取已发送 ID
            sent_ids = state.load(col_name)

            # 2. 抓取列表
            try:
                notices = adapter.fetch_list(list_url)
            except Exception as e:
                print(f"  ❌ [{col_name}] 抓取失败: {e}")
                continue

            # 3. 过滤
            new_notices = [n for n in notices if n.id not in sent_ids]
            if not new_notices:
                print(f"  ℹ️ [{col_name}] 无新公告（共 {len(notices)} 条）")
                continue

            col_count = 0
            for notice in reversed(new_notices):
                if not notice.column_id:
                    notice.column_id = col_id

                print(f"  ✨ [{col_name}] 新公告: {notice.title}  ({notice.date})")

                # 5. 抓详情
                detail = None
                detail_ok = True
                try:
                    detail = adapter.fetch_detail(notice)
                    logger.info(
                        "详情长度: %d, 附件数: %d",
                        len(detail.text),
                        len(detail.attachments),
                    )
                except Exception as e:
                    logger.warning("抓取详情失败 (%s): %s", notice.url, e)
                    detail_ok = False

                # mixed/vpn 栏目详情失败 → 不 mark_seen，不推送
                if not detail_ok and _needs_vpn(col_auth):
                    print(f"  ⚠️ [{col_name}] 详情抓取失败（可能需要 VPN），跳过该条")
                    continue

                # 6. 保存快照
                snapshot_ok = not snapshot_enabled
                if snapshot is not None and detail is not None:
                    try:
                        saved_attachments = []
                        if detail.attachments:
                            attachments_dir = (
                                snapshot._snapshot_dir(notice) / "attachments"
                            )
                            saved_attachments = download_attachments(
                                detail.attachments,
                                attachments_dir,
                                session=col_session,
                            )
                            vpn_attachment_fail = any(
                                a.error and any(
                                    kw in (a.error or "").lower()
                                    for kw in ["proxy", "tunnel", "connection", "timeout"]
                                )
                                for a in saved_attachments
                            )
                            if vpn_attachment_fail and settings.vpn_required and not _alert_sent_for_run:
                                _alert_sent_for_run = True
                                notifier.send_alert(
                                    message="附件下载出现代理/VPN 错误，VPN 可能已掉线。请检查。",
                                    title="⚠ SEU-Monitor: 附件下载异常",
                                )
                        snapshot.save(notice, detail, saved_attachments)
                        snapshot_ok = True
                    except Exception as e:
                        snapshot_ok = False
                        logger.error("保存快照失败 (%s): %s", notice.id, e)

                # 7. 推送
                push_ok = False
                if not settings.dry_run:
                    text_summary = detail.text if detail and detail.text else None
                    push_ok = notifier.send(
                        column_name=col_name,
                        title=notice.title,
                        date_text=notice.date,
                        notice_url=notice.url,
                        text_summary=text_summary,
                    )

                # 8. mark_seen（仅当 snapshot 成功时才标记，防止数据丢失）
                if push_ok and snapshot_ok:
                    state.mark_seen(col_name, notice.id)
                    sent_ids.add(notice.id)
                    col_count += 1
                    time.sleep(1)
                elif push_ok and not snapshot_ok:
                    print(f"  ⚠ [{col_name}] 通知已发送但 snapshot 失败，不标记已见")

            print(f"  ✅ [{col_name}] 处理完毕，新增 {col_count} 条。")
            total_new += col_count

    print(f"\n\U0001f3af 扫描结束，共新增 {total_new} 条公告。")
    return total_new


def load_config(path: str) -> Tuple[list, dict]:
    """加载 sites.yaml 配置。

    Returns:
        (sites_list, raw_yaml_dict)
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    sites = raw.get("sites", [])
    if not sites:
        logger.warning("配置文件中未定义任何站点")
    return sites, raw


def run_check(settings: Optional[Settings] = None) -> bool:
    """只做配置加载和健康检查，不抓取公告。"""
    if settings is None:
        settings = Settings.from_env_and_yaml()

    print("SEU-Monitor 健康检查")
    print("=" * 40)
    print(f"  配置文件:     {settings.config_path}")
    print(f"  STORE_ROOT:   {settings.store_root}")
    print(f"  SNAPSHOT_ROOT:{settings.snapshot_root}")
    print(f"  FEISHU:       {'已配置' if settings.feishu_webhook else '未配置'}")
    print(f"  HTTP_PROXY:   {settings.http_proxy or '(无)'}")
    print(f"  HTTPS_PROXY:  {settings.https_proxy or '(无)'}")
    print(f"  VPN_CHECK_URL:{settings.vpn_check_url or '(无)'}")
    print(f"  VPN_ENABLED:  {settings.vpn_enabled}")
    print(f"  VPN_REQUIRED: {settings.vpn_required}")
    print(f"  VPN_FAIL_FAST:{settings.vpn_fail_fast}")
    print(f"  超时:         {settings.request_timeout}s")
    print()

    try:
        sites, raw_yaml = load_config(settings.config_path)
    except FileNotFoundError:
        print(f"  ❌ 配置文件未找到: {settings.config_path}")
        return False
    except Exception as e:
        print(f"  ❌ 配置加载失败: {e}")
        return False

    print(f"  站点数: {len(sites)}")
    all_ok = True
    for site in sites:
        sid = site.get("id", "?")
        auth = site.get("auth", "public")
        name = site.get("name", sid)
        cols = len(site.get("columns", []))
        print(f"    - {sid} ({name}) auth={auth} 栏目={cols}")

    print()
    if settings.vpn_check_url:
        print("  VPN 健康检查...")
        proxy_url = settings.effective_vpn_proxy
        ok, msg = check_vpn_verbose(
            check_url=settings.vpn_check_url,
            proxy_url=proxy_url,
            timeout=settings.request_timeout,
        )
        status = "✅" if ok else "❌"
        print(f"    {status} {msg}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("✅ 所有检查通过")
    else:
        print("⚠️  部分未通过")
    return all_ok


def run_check_vpn(settings: Optional[Settings] = None) -> bool:
    """强制 VPN 健康检查，无论配置如何。

    Returns:
        True 可用，False 不可用。
    """
    if settings is None:
        settings = Settings.from_env_and_yaml()

    check_url = settings.vpn_check_url
    proxy_url = settings.effective_vpn_proxy

    print("SEU-Monitor VPN 健康检查")
    print("=" * 40)
    print(f"  VPN_CHECK_URL: {check_url}")
    print(f"  代理:          {proxy_url or '(无)'}")
    print(f"  VPN_REQUIRED:  {settings.vpn_required}")
    print(f"  VPN_FAIL_FAST: {settings.vpn_fail_fast}")
    print()

    ok, msg = check_vpn_verbose(
        check_url=check_url,
        proxy_url=proxy_url,
        timeout=settings.request_timeout,
    )

    if ok:
        print(f"  ✅ {msg}")
        return True
    else:
        print(f"  ❌ {msg}")
        if check_url:
            print()
            print("  手动检查命令:")
            print(f"    curl -k -I -x {proxy_url or 'http://127.0.0.1:8888'} --max-time 20 {check_url}")
        return False
